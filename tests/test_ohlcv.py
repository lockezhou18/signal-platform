"""OHLCV cache + fetch tests — hermetic, no network."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from signal_platform.data import ohlcv


def _fake_df(rows: int = 30) -> pd.DataFrame:
    """Minimal OHLCV frame with yfinance-style columns."""
    idx = pd.date_range("2024-01-01", periods=rows, freq="B")
    return pd.DataFrame(
        {
            "Open": range(rows),
            "High": [i + 1 for i in range(rows)],
            "Low": [i - 1 for i in range(rows)],
            "Close": [i + 0.5 for i in range(rows)],
            "Volume": [1_000_000] * rows,
        },
        index=idx,
    )


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the OHLCV cache to a temp dir so tests don't touch ~."""
    monkeypatch.setattr(ohlcv, "CACHE_ROOT", tmp_path / "ohlcv")
    return tmp_path / "ohlcv"


def test_fetch_ohlcv_writes_cache_on_miss(cache_dir: Path) -> None:
    fake = _fake_df()

    class _StubTicker:
        def history(self, **kwargs: object) -> pd.DataFrame:
            return fake

    with patch("signal_platform.data.ohlcv.yf.Ticker", return_value=_StubTicker()):
        df = ohlcv.fetch_ohlcv("FAKE", period="1y")

    assert not df.empty
    assert (cache_dir / "FAKE.parquet").exists()


def test_fetch_ohlcv_cache_hit_skips_network(cache_dir: Path) -> None:
    fake = _fake_df()
    cache_dir.mkdir(parents=True, exist_ok=True)
    fake.to_parquet(cache_dir / "CACHED.parquet")

    with patch("signal_platform.data.ohlcv.yf.Ticker") as mock_ticker:
        df = ohlcv.fetch_ohlcv("CACHED", period="1y")

    assert not df.empty
    mock_ticker.assert_not_called(), "fresh cache must bypass yfinance"


def test_fetch_ohlcv_raises_on_empty_yfinance_response(cache_dir: Path) -> None:
    class _EmptyTicker:
        def history(self, **kwargs: object) -> pd.DataFrame:
            return pd.DataFrame()

    with (
        patch("signal_platform.data.ohlcv.yf.Ticker", return_value=_EmptyTicker()),
        pytest.raises(RuntimeError, match="empty frame"),
    ):
        ohlcv.fetch_ohlcv("BADSYM", period="1y")


def test_fetch_universe_skips_failures(cache_dir: Path) -> None:
    fake = _fake_df()

    call_count = {"n": 0}

    class _MixedTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **kwargs: object) -> pd.DataFrame:
            call_count["n"] += 1
            if self.symbol == "BAD":
                raise ValueError("simulated yfinance failure")
            return fake

    with patch("signal_platform.data.ohlcv.yf.Ticker", side_effect=_MixedTicker):
        result = ohlcv.fetch_universe(["AAA", "BBB", "BAD", "CCC"], period="1y", max_workers=2)

    assert set(result.keys()) == {"AAA", "BBB", "CCC"}
    assert "BAD" not in result


def test_fetch_universe_empty_input_returns_empty() -> None:
    assert ohlcv.fetch_universe([], period="1y") == {}
