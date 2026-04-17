"""Universe fetcher tests — no network, no yfinance."""

from __future__ import annotations

import pytest

from signal_platform.data import universe


def test_sp500_snapshot_returns_sorted_deduped_list() -> None:
    tickers = universe.get_universe("sp500")
    assert len(tickers) >= 30, "snapshot should have at least 30 names"
    assert tickers == sorted(set(tickers)), "must be sorted + deduplicated"
    # Core wheel universe present
    for required in ("SPY", "QQQ", "IWM", "MSFT"):
        assert required in tickers


def test_wheel_alias_returns_explicit_list() -> None:
    assert universe.get_universe("wheel") == sorted(["SPY", "QQQ", "IWM", "MSFT"])


def test_mega_slice_is_subset_of_sp500() -> None:
    sp500 = set(universe.get_universe("sp500"))
    mega = set(universe.get_universe("mega"))
    assert mega.issubset(sp500)
    assert len(mega) <= 15


def test_unknown_universe_raises() -> None:
    with pytest.raises(ValueError, match="unknown universe"):
        universe.get_universe("russell9000")


def test_case_insensitive() -> None:
    a = universe.get_universe("SP500")
    b = universe.get_universe("sp500")
    assert a == b
