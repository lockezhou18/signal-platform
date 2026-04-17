"""Composite scorer tests — equal-weight baseline + Grinold residualization."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from signal_platform.signals.scorer import (
    composite_equal_weight,
    composite_grinold_residualized,
)


def _factors_latest(n_symbols: int = 10, n_factors: int = 3, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        rng.normal(0, 1, size=(n_symbols, n_factors)),
        index=[f"S{i:02d}" for i in range(n_symbols)],
        columns=[f"f{i}" for i in range(n_factors)],
    )


def _ic_history_wide(
    n_windows: int, factors: list[str], mean_per_factor: dict[str, float], seed: int = 0
) -> pd.DataFrame:
    """Build a synthetic IC history with a known mean IC per factor."""
    rng = np.random.default_rng(seed)
    data: dict[str, np.ndarray] = {}
    for f in factors:
        data[f] = rng.normal(mean_per_factor.get(f, 0.0), 0.02, n_windows)
    idx = pd.date_range("2024-01-07", periods=n_windows, freq="W-SUN")
    return pd.DataFrame(data, index=idx)


def test_equal_weight_produces_uniform_weights() -> None:
    f = _factors_latest(n_factors=4)
    result = composite_equal_weight(f)
    assert result.method == "equal_weight"
    # All weights should be 1/N
    expected = 1.0 / 4
    assert np.allclose(result.weights["weight"], expected)


def test_equal_weight_on_empty_frame_returns_empty() -> None:
    f = pd.DataFrame()
    result = composite_equal_weight(f)
    assert result.score.empty
    assert result.weights.empty


def test_grinold_weights_favor_factor_with_higher_residual_ic() -> None:
    """A factor with systematically higher IC should get a larger weight."""
    factors = ["strong", "weak"]
    ic_history = _ic_history_wide(
        n_windows=52,
        factors=factors,
        mean_per_factor={"strong": 0.08, "weak": 0.01},
        seed=42,
    )
    f = _factors_latest(n_factors=2)
    f.columns = pd.Index(factors)

    result = composite_grinold_residualized(f, ic_history, shrinkage=0.0)
    w = result.weights["weight"]
    assert w["strong"] > w["weak"], f"strong={w['strong']}, weak={w['weak']}"


def test_grinold_shrinkage_one_approximates_equal_weight() -> None:
    """With shrinkage=1, the composite should collapse to equal weights."""
    factors = ["a", "b", "c"]
    ic_history = _ic_history_wide(
        n_windows=52,
        factors=factors,
        mean_per_factor={"a": 0.1, "b": 0.03, "c": 0.05},
    )
    f = _factors_latest(n_factors=3)
    f.columns = pd.Index(factors)

    result = composite_grinold_residualized(f, ic_history, shrinkage=1.0)
    w = result.weights["weight"]
    expected = 1.0 / 3
    assert np.allclose(w.to_numpy(), expected, atol=1e-9), (
        f"with shrinkage=1 expected uniform {expected}, got {w.to_dict()}"
    )


def test_grinold_clamps_negative_residual_ic_to_zero() -> None:
    """Factors with persistently negative IC should get 0 weight (or near-0 under shrinkage)."""
    factors = ["good", "bad"]
    ic_history = _ic_history_wide(
        n_windows=52,
        factors=factors,
        mean_per_factor={"good": 0.08, "bad": -0.08},
        seed=7,
    )
    f = _factors_latest(n_factors=2)
    f.columns = pd.Index(factors)

    result = composite_grinold_residualized(f, ic_history, shrinkage=0.0)
    w = result.weights["weight"]
    # With residualization + negative clamp, 'bad' gets 0 raw; shrinkage=0 means no blending
    assert w["bad"] == pytest.approx(0.0, abs=1e-9)
    assert w["good"] == pytest.approx(1.0, abs=1e-9)


def test_grinold_raises_when_residuals_collapse_to_zero() -> None:
    """Identical IC across factors → residuals are exactly zero → raw weights
    collapse. With shrinkage=0 there is nothing to fall back on.

    Using constants (not noisy means) because residualization + noise can
    produce one sign-positive result by chance — the intent is to test the
    ``all-non-positive-with-no-shrinkage`` guard, which requires deterministic
    zero residuals.
    """
    idx = pd.date_range("2024-01-07", periods=20, freq="W-SUN")
    ic_history = pd.DataFrame(
        {"a": [-0.05] * 20, "b": [-0.05] * 20},
        index=idx,
    )
    f = _factors_latest(n_factors=2)
    f.columns = pd.Index(["a", "b"])

    with pytest.raises(ValueError, match="regime-alert"):
        composite_grinold_residualized(f, ic_history, shrinkage=0.0)


def test_grinold_raises_on_empty_ic_history() -> None:
    f = _factors_latest(n_factors=2)
    with pytest.raises(ValueError, match="ic_history_wide is empty"):
        composite_grinold_residualized(f, pd.DataFrame())


def test_grinold_raises_on_empty_factors_latest() -> None:
    ic_history = _ic_history_wide(n_windows=10, factors=["a"], mean_per_factor={"a": 0.05})
    with pytest.raises(ValueError, match="factors_latest is empty"):
        composite_grinold_residualized(pd.DataFrame(), ic_history)


def test_grinold_weights_sum_to_one() -> None:
    factors = ["a", "b", "c", "d"]
    ic_history = _ic_history_wide(
        n_windows=52,
        factors=factors,
        mean_per_factor={"a": 0.08, "b": 0.02, "c": 0.06, "d": -0.01},
    )
    f = _factors_latest(n_factors=4)
    f.columns = pd.Index(factors)

    result = composite_grinold_residualized(f, ic_history, shrinkage=0.1)
    assert result.weights["weight"].abs().sum() == pytest.approx(1.0, abs=1e-9)


def test_grinold_score_is_finite_for_all_symbols() -> None:
    factors = ["a", "b"]
    ic_history = _ic_history_wide(
        n_windows=52,
        factors=factors,
        mean_per_factor={"a": 0.08, "b": 0.03},
    )
    f = _factors_latest(n_symbols=15, n_factors=2)
    f.columns = pd.Index(factors)

    result = composite_grinold_residualized(f, ic_history)
    assert result.score.notna().all()
    assert len(result.score) == 15
