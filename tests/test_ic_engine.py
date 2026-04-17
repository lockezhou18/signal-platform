"""Cross-sectional IC engine tests — synthetic, deterministic, no network."""

from __future__ import annotations

import numpy as np
import pandas as pd

from signal_platform.signals.ic_engine import (
    aggregate_ic,
    cross_sectional_ic,
)


def _ohlcv(close: pd.Series) -> pd.DataFrame:
    """Minimal OHLCV frame around a given close series."""
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": [1_000_000] * len(close),
        },
        index=close.index,
    )


def _synthetic_universe(n_symbols: int, n_days: int, seed: int = 0) -> dict[str, pd.DataFrame]:
    """Universe where each symbol has an independent GBM close series."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n_days, freq="B")
    result: dict[str, pd.DataFrame] = {}
    for i in range(n_symbols):
        returns = rng.normal(0.0005, 0.015, n_days)
        close = pd.Series(100 * np.exp(np.cumsum(returns)), index=idx)
        result[f"S{i:02d}"] = _ohlcv(close)
    return result


def _rank_by_realized_factor(universe: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Factor fn: at each date, emit a factor equal to the *actual* 20-day
    forward return. Perfect predictor — used only for the perfect-IC test."""
    # In practice factor_fn takes a single symbol's df and returns factor df.
    # Here we rely on the caller to wrap this via partial application.
    raise NotImplementedError  # placeholder to document the intent


def _factor_matching_forward_return(df: pd.DataFrame, horizon: int = 5) -> pd.DataFrame:
    """Single-factor fn: returns df whose factor == actual h-day forward return.

    This makes the factor a perfect predictor of forward returns, so IC should
    approach 1.0 at every rebalance date.
    """
    fwd = df["Close"].shift(-horizon) / df["Close"] - 1.0
    return pd.DataFrame({"perfect": fwd}, index=df.index)


def _factor_noise(df: pd.DataFrame) -> pd.DataFrame:
    """Single-factor fn: noise that's UNCORRELATED across symbols.

    Seed is derived from the Close series sum (unique per GBM path in our
    synthetic universe). Seeding from ``df.index`` would collide across
    symbols — they share a calendar — and collapse the cross-section to
    a constant, making Spearman undefined (bug we hit in first pass).
    """
    seed = int(abs(df["Close"].sum() * 1e3)) % (2**32)
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"noise": rng.normal(0, 1, len(df))}, index=df.index)


def test_perfect_factor_yields_ic_near_one() -> None:
    """If the factor exactly equals forward return, Spearman should be ≈ 1.0."""
    universe = _synthetic_universe(n_symbols=30, n_days=200)
    res = cross_sectional_ic(
        universe,
        factor_fn=lambda df: _factor_matching_forward_return(df, horizon=5),
        horizon=5,
        rebalance="W-SUN",
    )
    valid = res.long.dropna(subset=["ic"])
    assert len(valid) > 0, "expected some valid IC values"
    # Allow slight slack for alignment + end-of-series missing data
    assert valid["ic"].mean() > 0.99


def test_random_factor_yields_ic_near_zero() -> None:
    """Uncorrelated noise across symbols should give mean IC ≈ 0 within SE."""
    universe = _synthetic_universe(n_symbols=40, n_days=250, seed=1)
    res = cross_sectional_ic(
        universe,
        factor_fn=_factor_noise,
        horizon=5,
        rebalance="W-SUN",
    )
    valid = res.long.dropna(subset=["ic"])
    assert len(valid) >= 20
    mean = float(valid["ic"].mean())
    se = float(valid["ic"].std() / np.sqrt(len(valid)))
    # 3σ band — should almost always contain zero under the null
    assert abs(mean) < 3 * se + 0.05, f"mean IC={mean:.3f} SE={se:.3f} — suspiciously far from 0"


def test_coverage_below_threshold_emits_nan() -> None:
    """Force partial coverage by deleting factor values from half the symbols."""
    universe = _synthetic_universe(n_symbols=10, n_days=100)

    def sparse_factor(df: pd.DataFrame) -> pd.DataFrame:
        fwd = df["Close"].shift(-5) / df["Close"] - 1.0
        # Blank out every other row so coverage at each date is ≤ 50%
        fwd.iloc[::2] = np.nan
        return pd.DataFrame({"sparse": fwd}, index=df.index)

    res = cross_sectional_ic(
        universe,
        factor_fn=sparse_factor,
        horizon=5,
        rebalance="W-SUN",
        min_coverage=0.8,
    )
    # Some rows should be NaN due to coverage gate
    nan_count = res.long["ic"].isna().sum()
    assert nan_count > 0, "expected some NaN ICs from coverage gate"


def test_empty_universe_returns_empty_frames() -> None:
    res = cross_sectional_ic(
        {},
        factor_fn=_factor_noise,
        horizon=5,
    )
    assert res.long.empty
    assert res.wide.empty
    assert res.horizon == 5


def test_aggregate_ic_produces_per_factor_stats() -> None:
    universe = _synthetic_universe(n_symbols=25, n_days=200, seed=7)

    def two_factors(df: pd.DataFrame) -> pd.DataFrame:
        perfect = _factor_matching_forward_return(df, horizon=5)["perfect"]
        noise = _factor_noise(df)["noise"]
        return pd.DataFrame({"perfect": perfect, "noise": noise})

    res = cross_sectional_ic(universe, factor_fn=two_factors, horizon=5)
    # Use adaptive significance threshold (auto-derived 2σ for cross-section N)
    summary = aggregate_ic(res.long)
    assert not summary.empty
    # Both factors appear in the summary
    assert {"perfect", "noise"}.issubset(summary.index)
    # 'perfect' should dominate 'noise' in mean_ic by orders of magnitude
    assert summary.loc["perfect", "mean_ic"] > 10 * abs(summary.loc["noise", "mean_ic"])
    # 'perfect' should be statistically significant in almost every window;
    # 'noise' comparison is less strict because with small cross-sections
    # (N=25 here), even the 2σ band is ±0.4 — noise crosses it often enough
    # that a simple threshold check is weak. The mean_ic comparison above is
    # the robust discriminator.
    assert summary.loc["perfect", "pct_significant"] > 0.95
    # IR for 'perfect' is NaN (std=0 with a perfect predictor) — that's fine,
    # the economics question is "do we rank factors correctly" and mean_ic
    # already answers that.


def test_result_wide_pivot_is_consistent_with_long() -> None:
    universe = _synthetic_universe(n_symbols=15, n_days=150)

    def two_factors(df: pd.DataFrame) -> pd.DataFrame:
        perfect = _factor_matching_forward_return(df, horizon=5)["perfect"]
        noise = _factor_noise(df)["noise"]
        return pd.DataFrame({"perfect": perfect, "noise": noise})

    res = cross_sectional_ic(universe, factor_fn=two_factors, horizon=5)
    # Wide[date, 'perfect'] should equal the long row with same date+factor
    for ts in res.wide.index[:3]:
        wide_val = res.wide.loc[ts, "perfect"]
        long_val = res.long[(res.long["timestamp"] == ts) & (res.long["factor"] == "perfect")][
            "ic"
        ].iloc[0]
        # Both NaN or both equal
        if pd.isna(wide_val):
            assert pd.isna(long_val)
        else:
            assert wide_val == long_val
