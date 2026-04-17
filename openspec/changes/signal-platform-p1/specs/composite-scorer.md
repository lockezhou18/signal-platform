# Spec: composite-scorer

## Purpose

Convert a collection of cross-sectional factor signals into a single composite score per symbol, using Grinold's residualized weighting (step 9 of the 11-step alpha combination procedure). Provide equal-weight as comparison baseline — the explicit anti-pattern this project rejects.

## Interface

```python
def composite_equal_weight(
    factors: pd.DataFrame,        # rows = symbols, cols = factor names
    mask_valid: pd.Series = None, # optional boolean mask of "factors to include"
) -> pd.Series:
    """Baseline composite — z-score each factor, sum. Explicitly NOT the recommended scoring;
    exists for A/B comparison. Published as 'composite_equal_weight' in output."""

def composite_grinold_residualized(
    factors: pd.DataFrame,
    ic_summary: pd.DataFrame,      # per-factor IC history from factor-ic-engine
    lookback_windows: int = 52,
    shrinkage: float = 0.1,
) -> tuple[pd.Series, pd.DataFrame]:
    """
    Grinold step-9 residualized composite.

    Procedure (per R. Grinold / B. Kahn, "Active Portfolio Management", Ch 6;
    retail-friendly summary at team-shared/knowledge/sources/alpha-combination-20260416/):

    1. Demean each factor serially (over lookback_windows).
    2. Standardize each factor by its stdev.
    3. Cross-sectionally demean at each timestamp.
    4. Regress each factor's expected forward return on the cross-demeaned matrix
       without intercept — residuals = each factor's independent contribution.
    5. Weight w_i = η × ε_i / σ_i (shrinkage via `shrinkage` param).
    6. Normalize Σ|w_i| = 1.
    7. Score = Σ w_i × f_i (evaluated at current timestamp).

    Returns:
      - pd.Series composite_score keyed by symbol
      - pd.DataFrame weights_trace with columns [factor, weight, residual_ic, noise_sigma]
    """

def compare_weightings(
    factors: pd.DataFrame,
    ic_summary: pd.DataFrame,
) -> dict[str, pd.Series]:
    """Return both scores keyed by method name. Diagnostic output only."""
```

## Decisions locked

- **Residualization uses rolling 52-week window** of historical IC, not full-period. Matches Grinold-Kahn recommendation for non-stationarity.
- **Shrinkage default 0.1** — moderate regularization. Extreme residualized weights get pulled toward equal-weight by this factor.
- **Weights are NOT negative by default.** If a factor's residual IC is negative, its weight is clamped to 0 and a flag is raised. (Short-signal composites deferred to future phase.)

## Anti-patterns explicitly rejected

Per `team-shared/knowledge/notes/note-20260416-120000-alpha-combination-vs-israelov.md` and codified in `quant-advisor.md`:

- **Equal-weight factor composites** (implemented as `composite_equal_weight` purely for A/B). Grinold-illiterate.
- **Ranking without IC awareness.** "Top-quintile on vol_std_10" is not a strategy; we must rank on *residualized* signal.
- **Overfitting IC to in-sample.** The `lookback_windows` parameter is set to 52w specifically so that each weight uses IC estimated on data that predates (by at least one rebalance) the scoring moment.

## Failure modes

| Failure | Response |
|---|---|
| Insufficient IC history (<10 valid windows for some factor) | Drop that factor from composite; log; emit diagnostic |
| All factors have negative residual IC | Refuse to score; set status `regime-alert`; log |
| Factor matrix rank-deficient | Fall back to equal-weight; emit diagnostic; status `measurement-only` |
| Composite score is NaN for all symbols | Block emit; raise alert |

## Observability requirements

- `signal_platform_composite_weight{factor}` gauge — current weight per factor in residualized composite
- `signal_platform_composite_method_active` gauge labeled `method` — 1 for active method, 0 for baseline; helps Grafana show which composite is live
- `signal_platform_dropped_factor_total{reason}` counter — factors dropped this run (negative IC, NaN, etc.)

## Testing

- Unit: on 2 perfectly-correlated factors + 1 uncorrelated, residualization should weight only 1 of the correlated pair + the uncorrelated factor; equal-weight would weight all 3 equally
- Unit: on a factor with 0 IC in the summary, weight should be 0 (or near 0 after shrinkage)
- Unit: Σ|weights| ≈ 1 within float tolerance
- A/B: equal-weight and Grinold outputs should differ meaningfully on real data (Pearson corr < 0.99)

## Known limitations

- **Single composite per run** (no ensemble across horizons yet).
- **No factor-family normalization.** If 8 factors are momentum variants + 1 mean-reversion, the momentum cluster dominates unless residualization catches it fully. Stress-test by monitoring weights.
- **No risk-model layer.** We don't constrain composite for active-risk budget (Barra-style). P1 is raw signal; risk layer is P5+.
