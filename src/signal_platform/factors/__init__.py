"""Alpha factor library — Qlib Alpha158-inspired subset.

Pure functions: DataFrame in, DataFrame out. No framework dependency.
Derived from the same implementation that powers
``financial-engine/trade/factors.py``; kept in sync manually until the
underlying repo is refactored into a shared package.

Reference: https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/handler.py

Input DataFrames require the yfinance-style columns: Open, High, Low, Close, Volume.
Column order is not important; presence is.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# === PRICE-VOLUME FACTORS ===


def return_features(df: pd.DataFrame, windows: list[int] | None = None) -> pd.DataFrame:
    """Multi-horizon simple returns."""
    if windows is None:
        windows = [1, 2, 3, 5, 10, 20, 30, 60]
    f = pd.DataFrame(index=df.index)
    for w in windows:
        f[f"roc_{w}"] = df["Close"].pct_change(w)
    return f


def volatility_features(df: pd.DataFrame, windows: list[int] | None = None) -> pd.DataFrame:
    """Rolling return volatility at multiple horizons."""
    if windows is None:
        windows = [5, 10, 20, 30, 60]
    f = pd.DataFrame(index=df.index)
    returns = df["Close"].pct_change()
    for w in windows:
        f[f"std_{w}"] = returns.rolling(w).std()
    return f


def volume_features(df: pd.DataFrame, windows: list[int] | None = None) -> pd.DataFrame:
    """Volume ratio + rolling volume volatility."""
    if windows is None:
        windows = [5, 10, 20, 30, 60]
    f = pd.DataFrame(index=df.index)
    for w in windows:
        rolling_mean = df["Volume"].rolling(w).mean()
        f[f"vol_ratio_{w}"] = df["Volume"] / rolling_mean
        f[f"vol_std_{w}"] = df["Volume"].rolling(w).std() / rolling_mean
    return f


def ma_features(df: pd.DataFrame, windows: list[int] | None = None) -> pd.DataFrame:
    """Moving average distance + crossover features."""
    if windows is None:
        windows = [5, 10, 20, 30, 60]
    f = pd.DataFrame(index=df.index)
    for w in windows:
        ma = df["Close"].rolling(w).mean()
        f[f"ma_{w}"] = (df["Close"] - ma) / ma
    if len(windows) >= 2:
        for i in range(len(windows) - 1):
            short_ma = df["Close"].rolling(windows[i]).mean()
            long_ma = df["Close"].rolling(windows[i + 1]).mean()
            f[f"ma_cross_{windows[i]}_{windows[i + 1]}"] = (short_ma - long_ma) / long_ma
    return f


# === MOMENTUM / MEAN-REVERSION FACTORS ===


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def momentum_factors(df: pd.DataFrame) -> pd.DataFrame:
    """RSI + MACD + Bollinger-position + 12-1 momentum."""
    f = pd.DataFrame(index=df.index)

    for period in [6, 12, 14, 24]:
        f[f"rsi_{period}"] = rsi(df["Close"], period)

    ema12 = df["Close"].ewm(span=12).mean()
    ema26 = df["Close"].ewm(span=26).mean()
    f["macd"] = ema12 - ema26
    f["macd_signal"] = f["macd"].ewm(span=9).mean()
    f["macd_hist"] = f["macd"] - f["macd_signal"]

    for w in [20, 30]:
        ma = df["Close"].rolling(w).mean()
        std = df["Close"].rolling(w).std()
        f[f"bb_pos_{w}"] = (df["Close"] - ma) / (2 * std).replace(0, np.nan)

    f["mom_12_1"] = df["Close"].pct_change(12) - df["Close"].pct_change(1)
    return f


def vwap_features(df: pd.DataFrame, windows: list[int] | None = None) -> pd.DataFrame:
    """Distance from rolling VWAP."""
    if windows is None:
        windows = [5, 10, 20]
    f = pd.DataFrame(index=df.index)
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    for w in windows:
        vwap = (typical_price * df["Volume"]).rolling(w).sum() / df["Volume"].rolling(w).sum()
        f[f"vwap_dist_{w}"] = (df["Close"] - vwap) / vwap
    return f


# === HIGH-LEVEL API ===


def compute_all_factors(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the full P1 factor set (~45 columns) for a single symbol.

    Deliberately excludes Qlib's kbar_features — the implementation in
    financial-engine uses Python ``max()`` on pandas Series which breaks
    vector semantics. Add a correct kbar implementation in a later phase.
    """
    return pd.concat(
        [
            return_features(df),
            volatility_features(df),
            volume_features(df),
            ma_features(df),
            momentum_factors(df),
            vwap_features(df),
        ],
        axis=1,
    )
