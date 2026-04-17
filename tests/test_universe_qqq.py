"""Tests for the qqq universe alias added in PR 4 polish."""

from __future__ import annotations

from signal_platform.data import universe


def test_qqq_alias_returns_nonempty_subset() -> None:
    tickers = universe.get_universe("qqq")
    assert 10 < len(tickers) <= 100, f"unexpected QQQ size: {len(tickers)}"
    # Core Nasdaq-100 anchors
    for required in ("AAPL", "MSFT", "NVDA", "AMZN"):
        assert required in tickers


def test_qqq_deduplicated_and_sorted() -> None:
    tickers = universe.get_universe("qqq")
    assert tickers == sorted(set(tickers))


def test_qqq_and_sp500_overlap() -> None:
    """QQQ's mega-cap tech names should also be in the sp500 snapshot."""
    qqq = set(universe.get_universe("qqq"))
    sp500 = set(universe.get_universe("sp500"))
    # NVDA / MSFT / AAPL / AMZN etc. live in both lists
    overlap = qqq & sp500
    assert len(overlap) >= 10


def test_qqq_case_insensitive() -> None:
    assert universe.get_universe("QQQ") == universe.get_universe("qqq")
