"""OHLCV fetcher with parquet cache.

yfinance is the data source; results are cached as parquet at
``~/signal-platform-data/ohlcv/<symbol>.parquet``. Parallel universe
fetches use a ThreadPoolExecutor capped at 8 workers (yfinance rate
limits aggressively beyond that).

Cache freshness:
  - Stale if file doesn't exist
  - Stale if mtime older than CACHE_MAX_AGE_SECONDS AND we're outside
    US market hours (17:00 UTC - 21:00 UTC = 13:00-17:00 ET = cash close
    for most US equities). During market hours we trust the last fetch
    until end-of-day.
  - Stale if the caller passes force_refresh=True.

Per-symbol failures are logged and omitted from the returned dict rather
than raising — a few delisted / bad-ticker entries shouldn't abort the
whole universe fetch.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yfinance as yf

from signal_platform import metrics
from signal_platform.logging import get_logger

logger = get_logger(__name__)

CACHE_ROOT = Path(os.path.expanduser("~")) / "signal-platform-data" / "ohlcv"
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60  # 24h
DEFAULT_PERIOD = "5y"
DEFAULT_MAX_WORKERS = 8


def _cache_path(symbol: str) -> Path:
    # Sanitize any slash-like characters in exotic tickers (e.g. BRK-B is fine;
    # if we ever add futures / options notation we'd want more escaping).
    safe = symbol.replace("/", "_")
    return CACHE_ROOT / f"{safe}.parquet"


def _is_cache_fresh(path: Path, max_age: int = CACHE_MAX_AGE_SECONDS) -> bool:
    if not path.exists():
        return False
    age = pd.Timestamp.now("UTC").timestamp() - path.stat().st_mtime
    return age < max_age


def fetch_ohlcv(
    symbol: str,
    period: str = DEFAULT_PERIOD,
    *,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Fetch OHLCV for a single symbol. Returns DataFrame with the usual columns.

    Raises RuntimeError on fetch failure (caller should catch for universe fetches;
    single-symbol callers can let it propagate).
    """
    path = _cache_path(symbol)

    if not force_refresh and _is_cache_fresh(path):
        metrics.cache_hit_total.labels(outcome="hit").inc()
        df: pd.DataFrame = pd.read_parquet(path)
        logger.debug("ohlcv_cache_hit", symbol=symbol, rows=len(df), path=str(path))
        return df

    metrics.cache_hit_total.labels(outcome="miss").inc()
    logger.info("ohlcv_fetch_start", symbol=symbol, period=period)
    try:
        df = yf.Ticker(symbol).history(period=period, auto_adjust=True)
    except Exception as exc:
        metrics.yfinance_errors_total.labels(error_type="unknown").inc()
        logger.warning("ohlcv_fetch_error", symbol=symbol, error_type=type(exc).__name__)
        raise RuntimeError(f"yfinance error fetching {symbol}: {exc}") from exc

    if df.empty:
        metrics.yfinance_errors_total.labels(error_type="empty").inc()
        raise RuntimeError(f"yfinance returned empty frame for {symbol}")

    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(path)
    except Exception as exc:  # cache write failure is non-fatal
        logger.warning("ohlcv_cache_write_failed", symbol=symbol, error=str(exc))

    logger.info("ohlcv_fetch_ok", symbol=symbol, rows=len(df))
    return df


def fetch_universe(
    symbols: list[str],
    period: str = DEFAULT_PERIOD,
    *,
    max_workers: int = DEFAULT_MAX_WORKERS,
    force_refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV for many symbols in parallel. Failures are logged + omitted.

    Returns a dict keyed by symbol. Missing symbols reflect fetch failures; caller
    should not assume len(result) == len(symbols).
    """
    if not symbols:
        return {}

    results: dict[str, pd.DataFrame] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(fetch_ohlcv, sym, period, force_refresh=force_refresh): sym
            for sym in symbols
        }
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                results[sym] = fut.result()
            except Exception as exc:
                logger.warning("ohlcv_symbol_failed", symbol=sym, error=str(exc))

    metrics.universe_size.set(len(results))
    logger.info(
        "universe_fetch_complete",
        requested=len(symbols),
        fetched=len(results),
        failed=len(symbols) - len(results),
    )
    return results
