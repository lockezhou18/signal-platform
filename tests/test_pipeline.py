"""End-to-end pipeline test with mocked yfinance — no network."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from signal_platform import metrics
from signal_platform.pipeline import run_once


def _fake_ohlcv(n_days: int = 400, seed: int = 0) -> pd.DataFrame:
    """Deterministic OHLCV with enough history for factors + IC windows."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-02", periods=n_days, freq="B")
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, n_days)))
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": rng.integers(500_000, 5_000_000, n_days),
        },
        index=idx,
    )


def test_run_once_produces_ranked_watchlist() -> None:
    """Pipeline runs end-to-end on a mocked 'mega' universe and returns a top-N list."""

    def fake_fetch(symbols: list[str], **_kwargs: object) -> dict[str, pd.DataFrame]:
        # Deterministic per-symbol paths — each symbol gets its own seed.
        return {sym: _fake_ohlcv(seed=hash(sym) & 0xFFFF) for sym in symbols}

    with patch("signal_platform.pipeline.fetch_universe", side_effect=fake_fetch):
        result = run_once(universe_name="mega", horizon=5, lookback_windows=30, top_n=5)

    # Mega universe has 15 names; we should have fetched roughly all of them
    assert result.n_fetched >= 10
    assert len(result.composite_score) >= 10
    assert len(result.top_n) == 5
    # Top-N should be sorted descending
    scores = [s for _, s in result.top_n]
    assert scores == sorted(scores, reverse=True)
    # IC summary should have been built (at least one factor with valid IC)
    assert not result.ic_summary.empty


def test_run_once_updates_e2e_probe() -> None:
    """After a successful run, the e2e probe gauge should be 1."""

    def fake_fetch(symbols: list[str], **_kwargs: object) -> dict[str, pd.DataFrame]:
        return {sym: _fake_ohlcv(seed=hash(sym) & 0xFFFF) for sym in symbols}

    # Reset first so we can observe the transition
    metrics.e2e_probe_ok.set(0)
    with patch("signal_platform.pipeline.fetch_universe", side_effect=fake_fetch):
        run_once(universe_name="mega", horizon=5, lookback_windows=30, top_n=3)

    # Prometheus-client gauges expose value via collect()[0].samples[0].value
    samples = list(metrics.e2e_probe_ok.collect())[0].samples
    assert samples[0].value == 1.0


def test_run_once_raises_on_empty_fetch() -> None:
    """If fetch returns nothing, downstream IC/score has nothing to work with."""

    def fake_empty(_symbols: list[str], **_kwargs: object) -> dict[str, pd.DataFrame]:
        return {}

    # Empty universe means IC result is empty → Grinold scorer raises
    # ('ic_history_wide is empty'); surface that as a pipeline failure.
    with (
        patch("signal_platform.pipeline.fetch_universe", side_effect=fake_empty),
        pytest.raises(ValueError),
    ):
        run_once(universe_name="mega", horizon=5, lookback_windows=30, top_n=3)
