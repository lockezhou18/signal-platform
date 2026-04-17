# Spec: walk-forward-validator

## Purpose

Validate that the composite scorer produces economically meaningful rankings by backtesting a simple top-decile weekly rebalance strategy across non-overlapping walk-forward windows. Status flag on the weekly watchlist emit depends directly on this validator's output.

## Interface

```python
def walk_forward_topk(
    universe_ohlcv: dict[str, pd.DataFrame],
    scorer: Callable[[pd.DataFrame, pd.DataFrame], pd.Series],
    train_months: int = 24,
    test_months: int = 3,
    top_k_pct: float = 0.10,
    rebalance: str = "W-SUN",
    fees_bps: int = 5,
) -> WalkForwardResult:
    """
    Roll through history:
      window_i.train = months [i*test_months, i*test_months + train_months)
      window_i.test  = months [i*test_months + train_months, (i+1)*test_months + train_months)
    Fit scorer on train, score universe on each test rebalance, long top K% by score,
    equal-weight within top-K, rebalance weekly, deduct fees_bps per turnover.
    Report per-window + aggregate Sharpe, return, max DD, turnover.
    """

@dataclass
class WalkForwardResult:
    per_window: pd.DataFrame   # rows per window: sharpe, return_pct, max_dd_pct, trades, turnover
    aggregate: dict            # mean_sharpe, median_sharpe, pct_positive_sharpe, etc.
    status: str                # 'validated' | 'measurement-only' | 'regime-alert'
```

## Status flag logic

| Aggregate Sharpe (mean across windows) | N windows | Status |
|---|---|---|
| ≥ 0.8 | ≥ 10 | `validated` |
| ≥ 0.5 AND < 0.8 | ≥ 10 | `measurement-only` |
| < 0.5 OR N < 10 | — | `regime-alert` |
| mean_sharpe changes sign in last 4 windows | — | override to `regime-alert` regardless |

Status is attached to the weekly watchlist emit. Downstream consumers (the quant-advisor, Binghua, future auto-action logic) **must check status** before trusting the ranking.

## Failure modes

| Failure | Response |
|---|---|
| Insufficient history for N ≥ 10 windows (need ~5y at 3mo test windows) | Emit `regime-alert` + diagnostic; do not block — partial info is better than none |
| Scorer raises on a window | Mark window failed, continue; if > 30% windows failed → `regime-alert` |
| Top-K portfolio has <5 names (universe too small) | Skip that window; log |
| Fees_bps > realistic (sanity check) | Warn if > 30bps; do not block |

## Reproducibility

- Random seed locked (no stochastic components expected; this is future-proofing)
- All pandas operations use explicit index sort before rebalance
- Results file contains run config: train_months, test_months, top_k_pct, rebalance, fees_bps, scorer_version, commit_sha

## Observability requirements

- `signal_platform_walkforward_sharpe{window_type=mean|median|latest}` gauge
- `signal_platform_walkforward_windows_total{outcome=passed|failed}` counter
- `platform_signal_platform_walkforward_status` gauge (enum encoded: 0=regime-alert, 1=measurement-only, 2=validated) — name matches the observability platform's `platform_*` probe prefix; this is the metric scraped by the observability tenant contract
- Status change events logged at INFO with before/after values

## Testing

- Unit: synthetic universe where scorer is perfectly predictive → Sharpe should be very high; validator returns `validated`
- Unit: synthetic universe where scorer is random → Sharpe ≈ 0; validator returns `regime-alert`
- Unit: status logic — inject known Sharpe values, confirm flag
- Integration: real 5y SPY/QQQ constituents with random scorer → roughly zero Sharpe within noise band

## Honest notes

- **Top-K weekly rebalance is a simple strategy** — not representative of what we'd actually trade. It's a *validator* for ranking quality, not a production strategy.
- Fees_bps=5 is conservative for liquid large caps at retail broker. We do NOT claim net-of-fees this is profitable; we claim the ranking has economic content.
- **`validated` status does NOT mean "trade this."** It means "the ranking has passed a minimal honesty bar to be worth human attention."
