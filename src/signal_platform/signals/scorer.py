"""Composite scorers: equal-weight baseline vs Grinold-residualized.

The equal-weight scorer exists solely as an A/B comparison — the
anti-pattern the project rejects (``team-shared/knowledge/notes/
note-20260416-120000-alpha-combination-vs-israelov.md``). The
Grinold-residualized scorer is what we actually run.

Residualization here is the pragmatic simplification of Grinold-Kahn
step 9: cross-sectionally demean per-timestamp IC to remove the shared
regime component, then rank factors by their *independent* mean IC
divided by their IC noise. This approximates the full covariance-
inverse optimal weighting without the numerical instability of
inverting a ~45×52 IC panel.

Weights are non-negative by default: a factor with negative residual
IC gets w_i = 0 and is logged as dropped. Short-signal composites are
deferred to a later phase.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from signal_platform import metrics
from signal_platform.logging import get_logger

logger = get_logger(__name__)

_FLOAT_TOL = 1e-9


@dataclass(frozen=True)
class ScoringResult:
    """Output of a composite scorer: per-symbol score + per-factor weight trace."""

    score: pd.Series  # index = symbol, values = composite score
    weights: pd.DataFrame  # index = factor; cols: weight, residual_ic, noise_sigma
    method: str  # 'equal_weight' | 'grinold_residualized'


def _zscore_cross_section(factors_latest: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional z-score per factor column. NaN-safe."""
    mean = factors_latest.mean(axis=0)
    std = factors_latest.std(axis=0).replace(0, np.nan)
    return (factors_latest - mean) / std


def composite_equal_weight(
    factors_latest: pd.DataFrame,
    _ic_history_wide: pd.DataFrame | None = None,
) -> ScoringResult:
    """Baseline composite: z-score each factor across symbols, sum with equal weights.

    Explicit anti-pattern reference implementation. Calls to this should be
    accompanied by a comparison run of ``composite_grinold_residualized`` so
    we can see empirically how much a Grinold-aware weighting changes the top
    of the ranking.

    Accepts (and ignores) ``_ic_history_wide`` so the scorer is drop-in
    compatible with the residualized variant's signature — required by
    walk_forward_topk's ``scorer`` parameter.
    """
    if factors_latest.empty:
        return ScoringResult(
            score=pd.Series(dtype=float),
            weights=pd.DataFrame(columns=["weight", "residual_ic", "noise_sigma"]),
            method="equal_weight",
        )

    z = _zscore_cross_section(factors_latest)
    n_factors = len(factors_latest.columns)
    w_value = 1.0 / n_factors if n_factors else 0.0
    score = z.fillna(0.0).sum(axis=1) * w_value

    weights = pd.DataFrame(
        {
            "weight": [w_value] * n_factors,
            "residual_ic": [np.nan] * n_factors,
            "noise_sigma": [np.nan] * n_factors,
        },
        index=list(factors_latest.columns),
    )

    logger.info(
        "scorer_equal_weight",
        n_symbols=len(factors_latest),
        n_factors=n_factors,
        weight_per_factor=w_value,
    )
    return ScoringResult(score=score, weights=weights, method="equal_weight")


def composite_grinold_residualized(
    factors_latest: pd.DataFrame,
    ic_history_wide: pd.DataFrame,
    lookback_windows: int = 52,
    shrinkage: float = 0.1,
) -> ScoringResult:
    """Residualized composite per Grinold step 9.

    Args:
        factors_latest: Rows = symbols (one trading date's cross-section),
            cols = factor names.
        ic_history_wide: Index = rebalance timestamps, cols = factor names,
            values = cross-sectional IC per window. Produced by
            ``ic_engine.CrossSectionalICResult.wide``.
        lookback_windows: Most recent N windows used for residualization.
        shrinkage: 0 = pure residualized weights; 1 = pure equal-weight.
            Regularizes against over-concentrating in a factor that just
            happens to have a big IC this window.

    Returns ``ScoringResult`` with per-factor trace.

    Raises:
        ValueError: no factors survive (all residual ICs ≤ 0 AND shrinkage=0,
            or insufficient IC history). Caller flips status to 'regime-alert'.
    """
    if factors_latest.empty:
        raise ValueError("factors_latest is empty")
    if ic_history_wide.empty:
        raise ValueError("ic_history_wide is empty — run the IC engine first")

    recent = ic_history_wide.tail(lookback_windows)

    # Cross-sectionally demean IC at each timestamp: subtract the mean across
    # factors. What remains per-factor is the idiosyncratic component — the
    # part that isn't explained by "it was a good/bad week for every factor".
    ic_demeaned = recent.sub(recent.mean(axis=1), axis=0)
    residual_mean_ic = ic_demeaned.mean(axis=0)
    ic_std = recent.std(axis=0).replace(0, np.nan)

    # Raw weight: residual mean IC per unit of IC noise. Clamp negatives to 0.
    raw = (residual_mean_ic / ic_std).fillna(0.0).clip(lower=0.0)

    # Track drops for observability
    for factor in raw.index:
        if raw[factor] <= _FLOAT_TOL:
            metrics.dropped_factor_total.labels(reason="non_positive_residual_ic").inc()

    if raw.abs().sum() <= _FLOAT_TOL and shrinkage <= _FLOAT_TOL:
        raise ValueError(
            "all factors have non-positive residual IC; regime-alert — "
            "refuse to score without shrinkage toward equal-weight."
        )

    # Normalize raw to Σ|w| = 1 (guarded against all-zero case)
    raw_sum = raw.abs().sum()
    normalized_raw = raw / raw_sum if raw_sum > _FLOAT_TOL else raw * 0.0

    # Equal-weight baseline over the SAME factor universe (so shrinkage has
    # something to blend toward even when raw weights collapse)
    n_factors = len(raw.index)
    equal = pd.Series(1.0 / n_factors, index=raw.index)

    # Blend: (1-s) × residualized + s × equal, then renormalize
    blended = (1.0 - shrinkage) * normalized_raw + shrinkage * equal
    weight = blended / blended.abs().sum()

    # Apply weights only to factors present in the current cross-section
    common = [f for f in weight.index if f in factors_latest.columns]
    if not common:
        raise ValueError("no overlap between ic_history factors and factors_latest columns")

    z = _zscore_cross_section(factors_latest[common])
    score = (z.fillna(0.0) * weight[common]).sum(axis=1)

    weights_trace = pd.DataFrame(
        {
            "weight": weight[common],
            "residual_ic": residual_mean_ic[common],
            "noise_sigma": ic_std[common],
        }
    )

    # Emit metrics for active factors
    for factor, row in weights_trace.iterrows():
        metrics.composite_weight.labels(factor=str(factor)).set(float(row["weight"]))

    logger.info(
        "scorer_grinold_residualized",
        n_symbols=len(factors_latest),
        n_factors_in=len(raw.index),
        n_factors_active=int((weight > _FLOAT_TOL).sum()),
        shrinkage=shrinkage,
        lookback_windows=lookback_windows,
    )
    return ScoringResult(score=score, weights=weights_trace, method="grinold_residualized")
