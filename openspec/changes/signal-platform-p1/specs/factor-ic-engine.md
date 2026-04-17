# Spec: factor-ic-engine

## Purpose

Compute cross-sectional rank IC (Spearman) for each factor × horizon, across a universe, at each rebalance timestamp. Distinct from time-series IC (already implemented in `financial-engine/trade/backtest/factor_ic.py`).

## Interface

```python
def cross_sectional_ic(
    universe_ohlcv: dict[str, pd.DataFrame],   # {symbol: DataFrame}
    factor_fn: Callable[[pd.DataFrame], pd.DataFrame],  # e.g. compute_all_factors
    horizon: int,                                # days forward
    rebalance: str = "W-SUN",                    # pandas freq string
    min_coverage: float = 0.6,                   # fraction of universe required per timestamp
) -> pd.DataFrame:
    """
    Returns long-format DataFrame with columns:
      - timestamp (period end)
      - factor
      - ic (Spearman rank correlation)
      - n_symbols (count of symbols in cross-section this period)
      - coverage (n_symbols / |universe|)
    """

def ic_summary(long_df: pd.DataFrame) -> pd.DataFrame:
    """
    Per factor: mean IC, median IC, IC std, IC t-stat, IR (mean/std).
    Report IC *time-stability* by computing IC on rolling 12-month windows;
    standard deviation of window-mean-ICs is the stability metric.
    """
```

## Core computation

At each rebalance timestamp `t`:
1. For each symbol s in universe, compute factor value `f[s, t]` from data up to and including t.
2. For each symbol s, compute forward return `r[s, t+h] = close[s, t+h] / close[s, t] - 1`.
3. Drop symbols missing either `f` or `r`.
4. Require `n >= min_coverage * |universe|` or emit NaN IC for this timestamp.
5. Compute Spearman rank correlation `ρ(rank(f), rank(r))` across remaining symbols.

## Failure modes

| Failure | Response |
|---|---|
| Factor function returns non-DataFrame | Raise TypeError with factor name; pipeline aborts |
| Factor value is all-NaN for timestamp | Emit NaN IC for that (factor, timestamp) |
| Coverage below `min_coverage` | Emit NaN IC; increment `ic_low_coverage_total` counter |
| Tied ranks (should be rare with continuous factors) | Spearman handles natively; no special case needed |
| Computation > 60s per factor | Log WARNING; proceed |

## Observability requirements

- `signal_platform_factor_ic{factor, horizon}` histogram — all IC values across timestamps
- `signal_platform_factor_ic_mean{factor, horizon}` gauge — trailing-12w mean IC per (factor, horizon)
- `signal_platform_ic_compute_duration_seconds{factor}` histogram
- `signal_platform_ic_low_coverage_total{horizon}` counter
- IC distribution **per run** logged as structlog event with factor/horizon breakdown

## Testing

- Unit: synthetic perfectly-ranked data → IC should be ≈ 1.0
- Unit: shuffled data → IC should be ≈ 0
- Unit: partial coverage → verify NaN emission
- Unit: single-timestamp correctness against `scipy.stats.spearmanr` direct call
- Regression: if we observed IC > 0.1 on factor X on date Y, re-running the same computation should reproduce exactly (determinism)

## Validation gate

IC summary must be produced for all 45 factors × 3 horizons before proceeding to scorer. If fewer than 90% of (factor, horizon) combinations have valid IC (N_windows >= 10), block scoring and emit diagnostic report.

## Known limitations

- **No neutralization** (sector, size, beta). Raw factors only. Added in P2+.
- **No Winsorization or z-scoring of factor values.** Rank IC is robust to outliers by construction but magnitude claims are limited.
- **Single-horizon per call.** To get 1d/5d/20d we invoke 3× sequentially.
