"""Cross-sectional Information Coefficient engine.

For each (factor, timestamp, horizon) triple, compute the Spearman rank
correlation between factor values across symbols and their forward h-day
returns. This is the estimand that matters for a ranking strategy:
"among today's universe, does this factor rank stocks in the same order
as their future returns?"

Distinct from time-series IC (see financial-engine/trade/backtest/
factor_ic.py) which answers "does this factor predict THIS symbol's
future returns?" — a different question with a different answer.

Design choices:
  - Wide-panel cache per factor (index=date, cols=symbol) built once per
    run; eliminates the O(symbols × factors × dates) nested-dict hot
    path that a naive implementation would use.
  - Weekly rebalance (W-SUN) by default to match the watchlist cadence.
    1d/5d/20d forward horizons target different use cases (near-term
    drift, weekly flip, monthly rebalance).
  - Coverage threshold — if fewer than min_coverage * |universe| symbols
    have both a valid factor value AND a valid forward return at a
    given rebalance date, we emit NaN rather than a low-sample IC.
    Default 0.6 — tuned so weekly rebalances on a 40-symbol universe
    require at least 24 valid pairs.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import ConstantInputWarning, spearmanr

from signal_platform import metrics
from signal_platform.logging import get_logger

logger = get_logger(__name__)

# Minimum valid-symbol count to bother computing Spearman; fewer than this and
# the statistic is dominated by noise even above the coverage %.
_MIN_ABSOLUTE_N = 8


@dataclass(frozen=True)
class CrossSectionalICResult:
    """IC results at long (per-cell) and wide (timestamp × factor) granularities."""

    long: pd.DataFrame  # cols: timestamp, factor, horizon, ic, n_symbols, coverage
    wide: pd.DataFrame  # index: timestamp; cols: factor; values: ic
    horizon: int


def _forward_return_panel(universe_ohlcv: dict[str, pd.DataFrame], horizon: int) -> pd.DataFrame:
    """Build index=date, cols=symbol DataFrame of h-day forward simple returns.

    For each symbol: r[t] = Close[t + horizon] / Close[t] - 1
    Uses shift(-horizon) so r[t] is the return earned FROM t.
    """
    series = {
        sym: df["Close"].shift(-horizon) / df["Close"] - 1.0 for sym, df in universe_ohlcv.items()
    }
    return pd.DataFrame(series)


def _factor_panels(
    universe_ohlcv: dict[str, pd.DataFrame],
    factor_fn: Callable[[pd.DataFrame], pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """Compute factors per symbol and pivot to {factor_name: date × symbol panel}."""
    per_symbol = {sym: factor_fn(df) for sym, df in universe_ohlcv.items()}
    if not per_symbol:
        return {}

    factor_columns = next(iter(per_symbol.values())).columns
    panels: dict[str, pd.DataFrame] = {}
    for factor in factor_columns:
        panels[factor] = pd.DataFrame({sym: df[factor] for sym, df in per_symbol.items()})
    return panels


def cross_sectional_ic(
    universe_ohlcv: dict[str, pd.DataFrame],
    factor_fn: Callable[[pd.DataFrame], pd.DataFrame],
    horizon: int,
    rebalance: str = "W-SUN",
    min_coverage: float = 0.6,
) -> CrossSectionalICResult:
    """Compute cross-sectional Spearman IC per (factor, rebalance-date)."""
    if not universe_ohlcv:
        logger.warning("xsec_ic_empty_universe")
        empty_long = pd.DataFrame(
            columns=["timestamp", "factor", "horizon", "ic", "n_symbols", "coverage"]
        )
        return CrossSectionalICResult(long=empty_long, wide=pd.DataFrame(), horizon=horizon)

    universe_size = len(universe_ohlcv)
    min_n = max(_MIN_ABSOLUTE_N, int(min_coverage * universe_size))

    logger.info(
        "xsec_ic_start",
        universe=universe_size,
        horizon=horizon,
        rebalance=rebalance,
        min_n=min_n,
    )

    fwd_panel = _forward_return_panel(universe_ohlcv, horizon)
    factor_panels = _factor_panels(universe_ohlcv, factor_fn)

    if not factor_panels:
        logger.warning("xsec_ic_no_factors")
        empty_long = pd.DataFrame(
            columns=["timestamp", "factor", "horizon", "ic", "n_symbols", "coverage"]
        )
        return CrossSectionalICResult(long=empty_long, wide=pd.DataFrame(), horizon=horizon)

    # Anchor rebalance calendar on the union of all observed dates.
    # Resampling 'W-SUN' over the full index gives us period-end timestamps
    # that we snap to the nearest prior trading day available in the index.
    combined_index = fwd_panel.index
    resampled = pd.Series(index=combined_index, dtype=float).resample(rebalance).last()
    rebalance_dates = resampled.index.intersection(combined_index)
    if rebalance_dates.empty:
        # Fallback: use every date present (for very short test series).
        rebalance_dates = combined_index

    rows: list[dict[str, object]] = []

    for t in rebalance_dates:
        fwd_cross = fwd_panel.loc[t] if t in fwd_panel.index else None
        if fwd_cross is None:
            continue

        for factor_name, panel in factor_panels.items():
            if t not in panel.index:
                rows.append(
                    _row(t, factor_name, horizon, np.nan, 0, 0.0, universe_size),
                )
                continue

            f_cross = panel.loc[t]
            joined = pd.concat([f_cross, fwd_cross], axis=1, keys=["f", "r"]).dropna()
            n = len(joined)
            coverage = n / universe_size

            if n < min_n:
                metrics.ic_low_coverage_total.labels(horizon=str(horizon)).inc()
                rows.append(_row(t, factor_name, horizon, np.nan, n, coverage, universe_size))
                continue

            # Suppress ConstantInputWarning on degenerate cross-sections — we
            # already emit NaN IC via the np.isnan check below.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ConstantInputWarning)
                ic_val, _ = spearmanr(joined["f"], joined["r"])

            if np.isnan(ic_val):  # constant inputs or all-tied ranks
                rows.append(_row(t, factor_name, horizon, np.nan, n, coverage, universe_size))
                continue

            metrics.factor_ic.labels(factor=factor_name, horizon=str(horizon)).observe(
                float(ic_val)
            )
            rows.append(_row(t, factor_name, horizon, float(ic_val), n, coverage, universe_size))

    long = pd.DataFrame(rows)
    if long.empty:
        return CrossSectionalICResult(long=long, wide=pd.DataFrame(), horizon=horizon)

    wide = long.pivot(index="timestamp", columns="factor", values="ic")

    logger.info(
        "xsec_ic_complete",
        horizon=horizon,
        rebalances=len(rebalance_dates),
        factors=len(factor_panels),
        rows=len(long),
        valid_cells=int(long["ic"].notna().sum()),
    )
    return CrossSectionalICResult(long=long, wide=wide, horizon=horizon)


def _row(
    t: pd.Timestamp,
    factor: str,
    horizon: int,
    ic: float,
    n: int,
    coverage: float,
    _universe_size: int,
) -> dict[str, object]:
    return {
        "timestamp": t,
        "factor": factor,
        "horizon": horizon,
        "ic": ic,
        "n_symbols": n,
        "coverage": coverage,
    }


def aggregate_ic(
    long: pd.DataFrame,
    significance_threshold: float | None = None,
) -> pd.DataFrame:
    """Per-factor summary statistics across rebalance windows.

    Returns DataFrame indexed by factor with:
      mean_ic, median_ic, std_ic, ir (mean/std), n_valid_windows, pct_significant

    ``significance_threshold`` defaults to 2 / sqrt(median cross-section N) —
    the 2σ band for a Spearman IC under the null at that N. Callers can
    override for a specific calibration (e.g. 0.05 for the ~1250-symbol
    S&P 500-style cross-section).
    """
    if long.empty:
        return pd.DataFrame()

    valid = long.dropna(subset=["ic"])
    if valid.empty:
        return pd.DataFrame()

    if significance_threshold is None:
        median_n = float(valid["n_symbols"].median())
        significance_threshold = 2.0 / np.sqrt(max(median_n, 1.0))

    grouped = valid.groupby("factor")["ic"]
    out = pd.DataFrame(
        {
            "mean_ic": grouped.mean(),
            "median_ic": grouped.median(),
            "std_ic": grouped.std(),
            "n_valid_windows": grouped.count(),
            "pct_significant": grouped.apply(lambda s: (s.abs() > significance_threshold).mean()),
        }
    )
    # Information Ratio of the IC series itself; zero-std → NaN (only one window).
    out["ir"] = out["mean_ic"] / out["std_ic"].replace(0, np.nan)
    return out.sort_values("mean_ic", key=lambda s: s.abs(), ascending=False)
