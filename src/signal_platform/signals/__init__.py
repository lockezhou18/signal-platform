"""Signal engine: cross-sectional IC, composite scoring."""

from signal_platform.signals.ic_engine import (
    CrossSectionalICResult,
    aggregate_ic,
    cross_sectional_ic,
)
from signal_platform.signals.scorer import (
    ScoringResult,
    composite_equal_weight,
    composite_grinold_residualized,
)
from signal_platform.signals.walk_forward import (
    WalkForwardResult,
    WalkForwardStatus,
    walk_forward_topk,
)

__all__ = [
    "CrossSectionalICResult",
    "ScoringResult",
    "WalkForwardResult",
    "WalkForwardStatus",
    "aggregate_ic",
    "composite_equal_weight",
    "composite_grinold_residualized",
    "cross_sectional_ic",
    "walk_forward_topk",
]
