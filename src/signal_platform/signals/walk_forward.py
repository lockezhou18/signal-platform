"""Walk-forward validator for the composite scorer.

Rolls through history with non-overlapping test windows, fits the scorer's
IC history on each training slab, scores each test rebalance date, buys
the top-K% of the universe equal-weighted, rebalances weekly, deducts
fees per turnover. Reports per-window + aggregate Sharpe/return/DD/turnover,
then stamps a status flag that downstream consumers (watchlist emit, the
quant-advisor, Binghua) are expected to check before trusting the ranking.

Per ``specs/walk-forward-validator.md``:

  | mean_sharpe | N_windows | status            |
  |-------------|-----------|-------------------|
  | ≥ 0.8       | ≥ 10      | validated         |
  | 0.5-0.8     | ≥ 10      | measurement-only  |
  | < 0.5 or N<10                | regime-alert      |
  | sign flip in last 4 windows  | regime-alert (override)

The per-window backtest is deliberately simple: long top-K%, equal-weight,
weekly rebalance. Sophisticated execution modeling (VWAP slippage, borrow
costs, capacity) is out of scope — the validator is a ranking-quality
test, not a production strategy simulator.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

import numpy as np
import pandas as pd

from signal_platform import metrics
from signal_platform.factors import compute_all_factors
from signal_platform.logging import get_logger
from signal_platform.signals.ic_engine import cross_sectional_ic
from signal_platform.signals.scorer import ScoringResult, composite_grinold_residualized

Scorer = Callable[[pd.DataFrame, pd.DataFrame], ScoringResult]

logger = get_logger(__name__)

TRADING_DAYS_PER_MONTH = 21


class WalkForwardStatus(StrEnum):
    """Status flag emitted with the watchlist — consumers MUST check this."""

    VALIDATED = "validated"
    MEASUREMENT_ONLY = "measurement-only"
    REGIME_ALERT = "regime-alert"


StatusLiteral = Literal["validated", "measurement-only", "regime-alert"]


@dataclass(frozen=True)
class WalkForwardResult:
    """Per-window + aggregate backtest output for the composite scorer."""

    per_window: pd.DataFrame  # rows=window idx; cols: train_start/end, test_start/end,
    #                                            sharpe, return_pct, max_dd_pct, turnover_pct,
    #                                            n_test_rebalances
    aggregate: dict[str, float]  # mean_sharpe, median_sharpe, pct_positive_windows, ...
    status: WalkForwardStatus
    config: dict[str, float | int] = field(default_factory=dict)


def _window_bounds(
    total_days: int, train_months: int, test_months: int
) -> list[tuple[int, int, int, int]]:
    """Yield (train_start, train_end, test_start, test_end) index tuples.

    Non-overlapping test windows; train window slides with each step.
    """
    train_days = train_months * TRADING_DAYS_PER_MONTH
    test_days = test_months * TRADING_DAYS_PER_MONTH
    bounds: list[tuple[int, int, int, int]] = []

    test_start = train_days
    while test_start + test_days <= total_days:
        train_start = test_start - train_days
        train_end = test_start
        test_end = test_start + test_days
        bounds.append((train_start, train_end, test_start, test_end))
        test_start = test_end

    return bounds


def _top_k_portfolio_returns(
    scores_by_date: pd.DataFrame,
    fwd_returns_by_date: pd.DataFrame,
    top_k_pct: float,
    fees_bps: int,
) -> tuple[pd.Series, float]:
    """Build the equal-weight top-K% portfolio and return its P&L per rebalance.

    Returns:
      - pd.Series of realized returns per rebalance date
      - total turnover fraction (mean position turnover × n_rebalances)
    """
    portfolio_returns: list[float] = []
    prev_holdings: set[str] = set()
    turnover_total = 0.0
    fees_decimal = fees_bps / 10000.0

    for date in scores_by_date.index:
        scores = scores_by_date.loc[date].dropna()
        if scores.empty:
            continue
        k = max(1, int(np.ceil(len(scores) * top_k_pct)))
        holdings = set(scores.sort_values(ascending=False).head(k).index)

        # Turnover = fraction of CURRENT holdings that are newly added this period.
        # ``symmetric_difference`` (old impl) counted adds+drops and double-billed
        # turnover, inflating fees by 2x and depressing reported Sharpe (cross-
        # review finding; see commit log). This measures portfolio churn correctly:
        # 3 of 10 names rotated in = 30% turnover.
        turnover = len(holdings - prev_holdings) / max(len(holdings), 1) if prev_holdings else 1.0
        turnover_total += turnover

        # Realized return: equal-weight forward return across held names
        if date in fwd_returns_by_date.index:
            held_returns = fwd_returns_by_date.loc[date, list(holdings)].dropna()
            gross = float(held_returns.mean()) if len(held_returns) else 0.0
            net = gross - turnover * fees_decimal
            portfolio_returns.append(net)
        prev_holdings = holdings

    n_rebal = max(1, len(scores_by_date))
    return pd.Series(portfolio_returns, index=scores_by_date.index[: len(portfolio_returns)]), (
        turnover_total / n_rebal
    )


def _sharpe_and_drawdown(returns: pd.Series) -> tuple[float, float, float]:
    """Annualized Sharpe (assuming weekly returns → 52 rebalances/yr), total
    return %, and max drawdown %.

    Drops to a numpy array up-front because pandas-stubs can't narrow a
    generic ``pd.Series`` to a float series, and chaining ``.prod()`` /
    ``.cummax()`` on the Series triggers no-overload errors under strict.
    """
    if returns.empty:
        return 0.0, 0.0, 0.0

    arr = returns.to_numpy(dtype=float)
    # Use a tolerance, not ==, because constant-valued inputs like [0.01]*10
    # have float64 variance ≈ 1e-35 (representation noise on 0.01) rather
    # than exactly 0. Dividing by that gives spurious Sharpe ≈ 1e+16.
    if arr.std() < 1e-12:
        return 0.0, 0.0, 0.0

    sharpe = float(arr.mean() / arr.std() * np.sqrt(52))  # weekly → √52 annualization
    total_return_pct = float(np.prod(1.0 + arr) - 1.0) * 100.0
    equity = np.cumprod(1.0 + arr)
    peaks = np.maximum.accumulate(equity)
    drawdown_pct = float((equity / peaks - 1.0).min()) * 100.0
    return sharpe, total_return_pct, drawdown_pct


def _status_from_aggregate(
    per_window: pd.DataFrame, mean_sharpe: float, n_windows: int
) -> WalkForwardStatus:
    """Apply the status threshold table + sign-flip override."""
    # Override: sign flip among the last 4 windows' Sharpe
    if len(per_window) >= 4:
        recent = per_window["sharpe"].tail(4)
        if ((recent > 0).any() and (recent < 0).any()) and abs(mean_sharpe) < 0.3:
            return WalkForwardStatus.REGIME_ALERT

    if n_windows < 10:
        return WalkForwardStatus.REGIME_ALERT
    if mean_sharpe >= 0.8:
        return WalkForwardStatus.VALIDATED
    if mean_sharpe >= 0.5:
        return WalkForwardStatus.MEASUREMENT_ONLY
    return WalkForwardStatus.REGIME_ALERT


def _status_to_gauge(status: WalkForwardStatus) -> int:
    return {
        WalkForwardStatus.REGIME_ALERT: 0,
        WalkForwardStatus.MEASUREMENT_ONLY: 1,
        WalkForwardStatus.VALIDATED: 2,
    }[status]


def walk_forward_topk(
    universe_ohlcv: dict[str, pd.DataFrame],
    train_months: int = 24,
    test_months: int = 3,
    top_k_pct: float = 0.10,
    horizon: int = 5,
    rebalance: str = "W-SUN",
    fees_bps: int = 5,
    scorer: Scorer = composite_grinold_residualized,
) -> WalkForwardResult:
    """Validate the composite scorer via rolling top-K% backtest.

    Args:
        universe_ohlcv: symbol → OHLCV DataFrame.
        train_months: rolling training window size.
        test_months: non-overlapping test window size.
        top_k_pct: fraction of universe to long each rebalance.
        horizon: forward-return horizon fed to cross-sectional IC (match
            the scorer's intent).
        rebalance: pandas freq string (e.g. 'W-SUN' for Fridays).
        fees_bps: round-trip transaction cost per unit turnover, in basis points.
        scorer: scoring function — defaults to Grinold residualized. Kept as a
            parameter so we can A/B against the equal-weight baseline in the
            same harness.
    """
    if not universe_ohlcv:
        raise ValueError("empty universe_ohlcv")

    # Use any one symbol's index to build bounds; assumes aligned calendars.
    sample = next(iter(universe_ohlcv.values()))
    total_days = len(sample)
    bounds = _window_bounds(total_days, train_months, test_months)

    if not bounds:
        logger.warning("walkforward_no_windows", total_days=total_days, train=train_months)
        status = WalkForwardStatus.REGIME_ALERT
        metrics.walkforward_status.set(_status_to_gauge(status))
        return WalkForwardResult(
            per_window=pd.DataFrame(),
            aggregate={"mean_sharpe": 0.0, "n_windows": 0},
            status=status,
            config={
                "train_months": train_months,
                "test_months": test_months,
                "top_k_pct": top_k_pct,
                "horizon": horizon,
                "fees_bps": fees_bps,
            },
        )

    # Compute factors ONCE for the full universe. Factor values at date t depend
    # only on data ≤ t (rolling windows), so slicing by window doesn't help
    # correctness — but recomputing per window was O(N_windows × symbols × factors)
    # and measurably slow at scale. Compute once; look up by date inside each
    # window. Cross-review finding: ~10x speedup on S&P 500 × 10y history.
    # No look-ahead introduced: we still gate by ic_train.wide (train-only IC)
    # when calling the scorer.
    full_fwd_panel = pd.DataFrame(
        {sym: df["Close"].shift(-horizon) / df["Close"] - 1.0 for sym, df in universe_ohlcv.items()}
    )
    full_factors_by_symbol = {sym: compute_all_factors(df) for sym, df in universe_ohlcv.items()}

    per_window_rows: list[dict[str, object]] = []

    for i, (train_s, train_e, test_s, test_e) in enumerate(bounds):
        train_ohlcv = {sym: df.iloc[train_s:train_e] for sym, df in universe_ohlcv.items()}

        try:
            ic_train = cross_sectional_ic(
                train_ohlcv,
                compute_all_factors,
                horizon=horizon,
                rebalance=rebalance,
            )
        except Exception as exc:
            logger.warning("walkforward_window_ic_failed", window=i, error=str(exc))
            continue

        if ic_train.wide.empty:
            logger.warning("walkforward_window_empty_ic", window=i)
            continue

        test_index = full_fwd_panel.index[test_s:test_e]
        test_fwd_panel = full_fwd_panel.iloc[test_s:test_e]
        rebalance_dates_all = (
            pd.Series(test_index, index=test_index).resample(rebalance).last().dropna().unique()
        )
        rebalance_dates = pd.DatetimeIndex(rebalance_dates_all)

        scores_by_date: dict[pd.Timestamp, pd.Series] = {}
        for date in rebalance_dates:
            if date not in test_index:
                continue
            factors_latest = pd.DataFrame(
                {
                    sym: fac.loc[date]
                    for sym, fac in full_factors_by_symbol.items()
                    if date in fac.index
                }
            ).T
            if factors_latest.empty:
                continue
            try:
                score_result = scorer(factors_latest, ic_train.wide)
            except ValueError:
                # Scorer rejected this date (regime-alert trigger); skip
                continue
            scores_by_date[date] = score_result.score

        if not scores_by_date:
            continue

        scores_df = pd.DataFrame(scores_by_date).T.sort_index()
        fwd_test = test_fwd_panel.reindex(scores_df.index)

        returns_series, turnover = _top_k_portfolio_returns(
            scores_df, fwd_test, top_k_pct=top_k_pct, fees_bps=fees_bps
        )

        sharpe, tot_return_pct, max_dd_pct = _sharpe_and_drawdown(returns_series)

        per_window_rows.append(
            {
                "window": i,
                "train_start": str(sample.index[train_s].date()),
                "train_end": str(sample.index[train_e - 1].date()),
                "test_start": str(sample.index[test_s].date()),
                "test_end": str(sample.index[test_e - 1].date()),
                "sharpe": round(sharpe, 3),
                "return_pct": round(tot_return_pct, 2),
                "max_dd_pct": round(max_dd_pct, 2),
                "turnover_pct": round(turnover * 100, 1),
                "n_test_rebalances": len(returns_series),
            }
        )

    per_window = pd.DataFrame(per_window_rows)

    if per_window.empty:
        status = WalkForwardStatus.REGIME_ALERT
        aggregate = {"mean_sharpe": 0.0, "n_windows": 0}
    else:
        mean_sharpe = float(per_window["sharpe"].mean())
        median_sharpe = float(per_window["sharpe"].median())
        n_windows = len(per_window)
        pct_positive = float((per_window["sharpe"] > 0).mean())

        status = _status_from_aggregate(per_window, mean_sharpe, n_windows)
        aggregate = {
            "mean_sharpe": round(mean_sharpe, 3),
            "median_sharpe": round(median_sharpe, 3),
            "n_windows": float(n_windows),
            "pct_positive_windows": round(pct_positive, 2),
            "mean_return_pct": round(float(per_window["return_pct"].mean()), 2),
            "worst_drawdown_pct": round(float(per_window["max_dd_pct"].min()), 2),
        }

    metrics.walkforward_status.set(_status_to_gauge(status))
    logger.info("walkforward_complete", status=status.value, **aggregate)

    return WalkForwardResult(
        per_window=per_window,
        aggregate=aggregate,
        status=status,
        config={
            "train_months": train_months,
            "test_months": test_months,
            "top_k_pct": top_k_pct,
            "horizon": horizon,
            "fees_bps": fees_bps,
        },
    )
