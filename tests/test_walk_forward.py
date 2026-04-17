"""Walk-forward validator tests.

The validator is the gate between "we computed a score" and "the score has
economic content." These tests check the status-flag logic directly (pure
function, deterministic) and the end-to-end walk on synthetic data where
we know the ground truth.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from signal_platform.signals.scorer import composite_equal_weight
from signal_platform.signals.walk_forward import (
    WalkForwardStatus,
    _sharpe_and_drawdown,
    _status_from_aggregate,
    _window_bounds,
    walk_forward_topk,
)

# --- Pure-function tests (status flag logic) ---


def test_status_regime_alert_when_too_few_windows() -> None:
    per_window = pd.DataFrame({"sharpe": [1.5] * 5})
    assert _status_from_aggregate(per_window, mean_sharpe=1.5, n_windows=5) == (
        WalkForwardStatus.REGIME_ALERT
    )


def test_status_validated_when_sharpe_high_and_enough_windows() -> None:
    per_window = pd.DataFrame({"sharpe": [1.0] * 12})
    assert _status_from_aggregate(per_window, mean_sharpe=1.0, n_windows=12) == (
        WalkForwardStatus.VALIDATED
    )


def test_status_measurement_only_in_mid_band() -> None:
    per_window = pd.DataFrame({"sharpe": [0.6] * 12})
    assert _status_from_aggregate(per_window, mean_sharpe=0.6, n_windows=12) == (
        WalkForwardStatus.MEASUREMENT_ONLY
    )


def test_status_regime_alert_when_sharpe_below_half() -> None:
    per_window = pd.DataFrame({"sharpe": [0.4] * 12})
    assert _status_from_aggregate(per_window, mean_sharpe=0.4, n_windows=12) == (
        WalkForwardStatus.REGIME_ALERT
    )


def test_status_override_on_sign_flip_in_recent_windows() -> None:
    # mean_sharpe looks OK-but-modest; but recent windows flip sign → regime alert
    per_window = pd.DataFrame({"sharpe": [0.8, 0.9, 0.8, -0.3, 0.5, -0.2]})
    result = _status_from_aggregate(per_window, mean_sharpe=0.25, n_windows=6)
    assert result == WalkForwardStatus.REGIME_ALERT


# --- Sharpe / drawdown helper ---


def test_sharpe_and_drawdown_zero_volatility() -> None:
    returns = pd.Series([0.01] * 10)  # constant returns → std=0 → Sharpe=0 by convention
    sharpe, ret, dd = _sharpe_and_drawdown(returns)
    assert sharpe == 0.0
    assert ret == 0.0
    assert dd == 0.0


def test_sharpe_and_drawdown_basic() -> None:
    # Positive drift + some vol. Sharpe should be positive; DD bounded.
    rng = np.random.default_rng(0)
    returns = pd.Series(rng.normal(0.01, 0.02, 52))  # 1 year of weekly
    sharpe, ret, dd = _sharpe_and_drawdown(returns)
    assert sharpe > 0
    assert -100 <= dd <= 0
    assert ret > 0


def test_sharpe_and_drawdown_empty_series_returns_zeros() -> None:
    sharpe, ret, dd = _sharpe_and_drawdown(pd.Series(dtype=float))
    assert (sharpe, ret, dd) == (0.0, 0.0, 0.0)


# --- Window bounds ---


def test_window_bounds_non_overlapping_test_periods() -> None:
    bounds = _window_bounds(total_days=1260, train_months=24, test_months=3)
    # 24 + 3 = 27 months ≈ 567 days; each next window slides by test_months (63 days)
    assert len(bounds) >= 10
    # Each test end should equal the NEXT test start
    for i in range(len(bounds) - 1):
        assert bounds[i][3] == bounds[i + 1][2]


def test_window_bounds_insufficient_history() -> None:
    bounds = _window_bounds(total_days=100, train_months=24, test_months=3)
    # Not enough history for even one full train+test → empty
    assert bounds == []


# --- End-to-end walk (synthetic universe) ---


def _ohlcv_from_close(close: pd.Series) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": [1_000_000] * len(close),
        },
        index=close.index,
    )


def _long_universe(
    n_symbols: int,
    n_days: int,
    seed: int = 0,
    drift: float = 0.0005,
) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-02", periods=n_days, freq="B")
    out = {}
    for i in range(n_symbols):
        returns = rng.normal(drift, 0.015, n_days)
        close = pd.Series(100 * np.exp(np.cumsum(returns)), index=idx)
        out[f"S{i:02d}"] = _ohlcv_from_close(close)
    return out


def test_walk_forward_end_to_end_produces_structured_result() -> None:
    """Smoke test: on synthetic data with enough history, walk_forward_topk
    returns a well-formed ``WalkForwardResult`` (non-empty per_window, all
    aggregate keys present, status is a valid enum).

    We do NOT assert the specific status here — what constitutes 'validated'
    depends on stochastic Sharpe realizations and is tested deterministically
    by ``_status_from_aggregate`` unit tests above. End-to-end tests with
    outcome assertions (e.g. "random scorer should not validate") are flaky
    across platforms — ~1-in-10 seeds accidentally lucked into validated on
    CI even with drift=0 (CI run #12).

    What this test proves: the end-to-end walk completes without crashing and
    produces a result callers can consume.
    """
    universe = _long_universe(n_symbols=15, n_days=1260, seed=11, drift=0.0005)

    result = walk_forward_topk(
        universe,
        train_months=12,
        test_months=3,
        top_k_pct=0.3,
        horizon=5,
        scorer=composite_equal_weight,
    )

    assert not result.per_window.empty
    assert {"window", "sharpe", "return_pct", "max_dd_pct", "turnover_pct"}.issubset(
        result.per_window.columns
    )
    for key in ("mean_sharpe", "n_windows"):
        assert key in result.aggregate
    assert isinstance(result.status, WalkForwardStatus)
    assert result.status in {
        WalkForwardStatus.VALIDATED,
        WalkForwardStatus.MEASUREMENT_ONLY,
        WalkForwardStatus.REGIME_ALERT,
    }


def test_walk_forward_config_is_preserved_in_result() -> None:
    universe = _long_universe(n_symbols=15, n_days=800, seed=5)
    result = walk_forward_topk(
        universe,
        train_months=12,
        test_months=3,
        top_k_pct=0.2,
        horizon=5,
        fees_bps=10,
        scorer=composite_equal_weight,
    )
    assert result.config["train_months"] == 12
    assert result.config["test_months"] == 3
    assert result.config["top_k_pct"] == 0.2
    assert result.config["fees_bps"] == 10


def test_walk_forward_empty_universe_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="empty universe_ohlcv"):
        walk_forward_topk({})


def test_walk_forward_no_look_ahead_on_completed_windows() -> None:
    """Windows that close before some date D must produce identical P&L
    regardless of what happens to the data after D.

    Mutate the Close series in the second half of the universe to pure
    garbage. Windows whose ``test_end`` date falls before the mutation
    point should come back bit-identical to the clean run; anything
    different means factor computation or IC training leaked future data.
    """
    clean = _long_universe(n_symbols=15, n_days=1260, seed=17, drift=0.0005)
    mutation_start = 700  # any day after a few full train+test cycles

    mutated: dict[str, pd.DataFrame] = {}
    for sym, df in clean.items():
        copy = df.copy()
        # Obliterate the back half: set Close to a single garbage constant so
        # factor rolling-window calcs + forward returns after this point are
        # completely different from the clean run.
        copy.iloc[mutation_start:, copy.columns.get_loc("Close")] = 9999.0
        mutated[sym] = copy

    r_clean = walk_forward_topk(
        clean,
        train_months=12,
        test_months=3,
        top_k_pct=0.3,
        horizon=5,
        scorer=composite_equal_weight,
    )
    r_mutated = walk_forward_topk(
        mutated,
        train_months=12,
        test_months=3,
        top_k_pct=0.3,
        horizon=5,
        scorer=composite_equal_weight,
    )

    # Windows whose test_end is strictly before the mutation point should
    # match exactly. Compare by test_end date rather than window index because
    # the mutated run might drop windows that land on garbage-only data.
    mutation_date = clean["S00"].index[mutation_start].date()
    clean_pre = r_clean.per_window[
        pd.to_datetime(r_clean.per_window["test_end"]).dt.date < mutation_date
    ]
    mutated_pre = r_mutated.per_window[
        pd.to_datetime(r_mutated.per_window["test_end"]).dt.date < mutation_date
    ]

    assert len(clean_pre) >= 1, "need at least one pre-mutation window to compare"
    assert len(clean_pre) == len(mutated_pre), (
        "pre-mutation window count differs — future data is leaking into "
        "window selection or factor computation"
    )
    # Sharpe, return, max_dd should match exactly for pre-mutation windows
    for col in ("sharpe", "return_pct", "max_dd_pct"):
        pd.testing.assert_series_equal(
            clean_pre[col].reset_index(drop=True),
            mutated_pre[col].reset_index(drop=True),
            check_names=False,
        )
