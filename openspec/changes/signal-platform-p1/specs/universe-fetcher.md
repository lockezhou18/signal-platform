# Spec: universe-fetcher

## Purpose

Provide a stable, cached list of ticker symbols for the screener's cross-section, plus daily OHLCV bars for each. No survivorship-bias correction (documented limitation for P1).

## Interface

```python
def get_universe(name: str) -> list[str]:
    """Return sorted list of tickers. name ∈ {'sp500', 'qqq100', 'r1000', 'custom:<path>'}."""

def fetch_ohlcv(symbol: str, period: str = "5y") -> pd.DataFrame:
    """Return OHLCV dataframe. Cached to ~/signal-platform-data/ohlcv/<symbol>.parquet.
    Cache is considered fresh if parquet exists and mtime < 24h ago AND we're outside US market hours;
    else re-fetch and overwrite."""

def fetch_universe(name: str, period: str = "5y") -> dict[str, pd.DataFrame]:
    """Fetch entire universe in parallel (ThreadPoolExecutor, max_workers=8).
    Returns dict keyed by symbol. Missing/failed symbols are logged and omitted (not raised)."""
```

## Data sources

- **S&P 500 constituents:** Wikipedia scrape (`https://en.wikipedia.org/wiki/List_of_S%26P_500_companies`) cached weekly. Fallback: hardcoded snapshot committed in `src/signal_platform/data/sp500_snapshot.txt`.
- **NASDAQ-100:** same pattern.
- **OHLCV:** `yfinance.Ticker(sym).history(period=period)`

## Failure modes

| Failure | Response |
|---|---|
| Wikipedia scrape fails | Fall back to committed snapshot; log warning; increment `universe_fetch_fallback_total` counter |
| yfinance 429 rate-limit | Exponential backoff: 2s, 4s, 8s. After 3 retries, mark symbol failed and continue |
| Symbol delisted / unknown | Log at WARNING; omit from universe; do not raise |
| Network timeout (>30s) | Abort this symbol; continue with rest |
| Parquet write failure | Log ERROR; continue (we have in-memory copy) |

## Observability requirements

- `signal_platform_universe_size` gauge updated on every fetch
- `signal_platform_yfinance_errors_total{error_type=rate_limit|timeout|delisted|other}` counter
- `signal_platform_universe_fetch_duration_seconds` histogram
- `signal_platform_cache_hit_total{outcome=hit|miss}` counter

## Testing

- Unit: mock yfinance, assert ThreadPoolExecutor dispatches N workers
- Integration: real fetch of SPY, QQQ, MSFT (small); assert parquet files exist + re-read matches first fetch
- Negative: inject 429; assert backoff respected; assert failure counter increments

## Known limitations

- **No survivorship-bias correction.** Using current S&P 500 membership, not point-in-time. Documented in every emit report.
- **No intraday granularity.** Daily bars only. 5-min / 1-min is deferred.
- **No corporate-action handling beyond yfinance defaults.** Splits/dividends adjusted by yfinance; mergers/delistings may cause gaps.
