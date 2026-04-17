"""Factor library tests — hermetic, deterministic synthetic data."""

from __future__ import annotations

import numpy as np
import pandas as pd

from signal_platform.factors import (
    compute_all_factors,
    ma_features,
    momentum_factors,
    return_features,
    rsi,
    volatility_features,
    volume_features,
    vwap_features,
)


def _make_df(rows: int = 200, seed: int = 42) -> pd.DataFrame:
    """Deterministic OHLCV frame shaped like yfinance output."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=rows, freq="B")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, rows)))
    high = close * (1 + np.abs(rng.normal(0, 0.005, rows)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, rows)))
    open_ = close + rng.normal(0, 0.5, rows)
    volume = rng.integers(500_000, 10_000_000, rows)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )


def test_return_features_shapes_and_values() -> None:
    df = _make_df()
    f = return_features(df)
    # 8 windows by default
    assert len(f.columns) == 8
    # roc_1 equals Close.pct_change()
    expected = df["Close"].pct_change()
    pd.testing.assert_series_equal(f["roc_1"], expected, check_names=False)
    # First-N values NaN for window N
    assert f["roc_5"].iloc[:5].isna().all()


def test_volatility_features_nonnegative() -> None:
    df = _make_df()
    f = volatility_features(df)
    # All finite values must be >= 0
    finite = f.replace([np.inf, -np.inf], np.nan).dropna()
    assert (finite >= 0).all().all()


def test_volume_features_has_ratio_and_std() -> None:
    df = _make_df()
    f = volume_features(df)
    assert any("vol_ratio_" in c for c in f.columns)
    assert any("vol_std_" in c for c in f.columns)


def test_ma_features_include_crossovers() -> None:
    df = _make_df()
    f = ma_features(df)
    # 5 distance-from-MA + 4 crossovers for default windows
    assert sum(c.startswith("ma_cross") for c in f.columns) == 4
    assert sum(c.startswith("ma_") and not c.startswith("ma_cross") for c in f.columns) == 5


def test_rsi_bounded_zero_to_hundred() -> None:
    df = _make_df()
    r = rsi(df["Close"])
    finite = r.dropna()
    assert (finite >= 0).all()
    assert (finite <= 100).all()


def test_momentum_factors_has_macd_triplet() -> None:
    df = _make_df()
    f = momentum_factors(df)
    assert {"macd", "macd_signal", "macd_hist"}.issubset(f.columns)
    # macd_hist = macd - macd_signal by construction (within float tolerance)
    diff = (f["macd_hist"] - (f["macd"] - f["macd_signal"])).abs().max()
    assert diff < 1e-9


def test_vwap_features_default_windows() -> None:
    df = _make_df()
    f = vwap_features(df)
    assert len(f.columns) == 3  # [5, 10, 20]
    assert {"vwap_dist_5", "vwap_dist_10", "vwap_dist_20"}.issubset(f.columns)


def test_compute_all_factors_returns_expected_column_count() -> None:
    """Sanity check — factor set should be ~45 columns.

    Exact count can drift if we add factors; guarding on a range keeps the
    test useful without being brittle.
    """
    df = _make_df()
    f = compute_all_factors(df)
    assert 40 <= len(f.columns) <= 55, f"unexpected factor count: {len(f.columns)}"


def test_compute_all_factors_index_matches_input() -> None:
    df = _make_df()
    f = compute_all_factors(df)
    pd.testing.assert_index_equal(f.index, df.index)
