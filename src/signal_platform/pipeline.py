"""End-to-end P1 pipeline: universe → fetch → IC → score.

One public entry point, ``run_once``, suitable for:
  - CLI invocation (``signal-platform run-once``)
  - Scheduler trigger (added in PR 3 — weekly Sun 6pm PT via APScheduler)
  - Ad-hoc test / REPL use

Intentionally lightweight in P1: no watchlist emit (PR 3), no walk-forward
status flag (PR 3), no per-horizon ensemble (P2+). Just measure and score.

Metrics updated per run:
  - platform_heartbeat_timestamp (set on entry)
  - signal_platform_last_run_status{run_type=<stage>}
  - signal_platform_last_run_duration_seconds{run_type=<stage>}
  - signal_platform_universe_size
  - signal_platform_factor_ic (via ic_engine)
  - signal_platform_composite_weight (via scorer)
  - platform_signal_platform_probe_ok (set to 1 only on complete success)
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import ParamSpec, TypeVar

import pandas as pd

from signal_platform import metrics
from signal_platform.data import fetch_universe, get_universe
from signal_platform.factors import compute_all_factors
from signal_platform.logging import bind_run_context, get_logger
from signal_platform.signals import (
    aggregate_ic,
    composite_grinold_residualized,
    cross_sectional_ic,
)

logger = get_logger(__name__)

_P = ParamSpec("_P")
_T = TypeVar("_T")


@dataclass(frozen=True)
class PipelineResult:
    """Summary of a single run — used by callers + tests."""

    universe: list[str]
    n_fetched: int
    ic_long: pd.DataFrame
    ic_summary: pd.DataFrame
    composite_score: pd.Series
    composite_weights: pd.DataFrame
    horizon: int
    top_n: list[tuple[str, float]]


def _stage(name: str, fn: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs) -> _T:
    """Time + status-track a pipeline stage.

    Re-raises on failure so callers can decide whether to continue with a
    partial result. Always records duration + success/failure status for
    observability.
    """
    start = time.time()
    try:
        result = fn(*args, **kwargs)
        metrics.last_run_status.labels(run_type=name).set(1)
        return result
    except Exception:
        metrics.last_run_status.labels(run_type=name).set(0)
        raise
    finally:
        metrics.last_run_duration_seconds.labels(run_type=name).set(time.time() - start)


def run_once(
    universe_name: str = "sp500",
    horizon: int = 5,
    lookback_windows: int = 52,
    top_n: int = 20,
) -> PipelineResult:
    """Run the full screener pipeline once and return the result.

    Side effects: updates Prometheus metrics. Logs every stage with run_id.
    Does NOT emit a watchlist file (PR 3) — caller decides what to do with
    the returned score.
    """
    run_id = bind_run_context()
    logger.info(
        "pipeline_start",
        run_id=run_id,
        universe=universe_name,
        horizon=horizon,
        lookback_windows=lookback_windows,
    )
    metrics.set_heartbeat()

    universe = _stage("universe", get_universe, universe_name)

    # Fetch all symbols. `fetch_universe` swallows per-symbol failures internally,
    # so this stage is expected to succeed even with a few delisted tickers.
    fetched = _stage("fetch", fetch_universe, universe)
    logger.info("pipeline_fetch_complete", requested=len(universe), fetched=len(fetched))

    ic_result = _stage(
        "ic",
        cross_sectional_ic,
        fetched,
        compute_all_factors,
        horizon,
    )
    ic_summary = _stage("ic_summary", aggregate_ic, ic_result.long)

    # Latest factors per symbol for scoring = the most recent row each symbol
    # has data for. We don't filter to a single timestamp because some symbols
    # may have shorter history; take each symbol's own latest observation.
    latest_rows = {
        sym: compute_all_factors(df).iloc[-1] for sym, df in fetched.items() if not df.empty
    }
    factors_latest = pd.DataFrame(latest_rows).T
    logger.info("pipeline_factors_latest", n_symbols=len(factors_latest))

    score_result = _stage(
        "score",
        composite_grinold_residualized,
        factors_latest,
        ic_result.wide,
        lookback_windows,
    )

    # Build top-N ranking; str() the keys because pandas types them as Hashable.
    top_series = score_result.score.sort_values(ascending=False).head(top_n)
    top_list: list[tuple[str, float]] = [(str(sym), float(val)) for sym, val in top_series.items()]
    logger.info("pipeline_top_n", top=top_list[:5], total_ranked=len(score_result.score))

    # Only mark e2e probe ok after the whole chain succeeded
    metrics.e2e_probe_ok.set(1)
    metrics.set_heartbeat()

    return PipelineResult(
        universe=universe,
        n_fetched=len(fetched),
        ic_long=ic_result.long,
        ic_summary=ic_summary,
        composite_score=score_result.score,
        composite_weights=score_result.weights,
        horizon=horizon,
        top_n=top_list,
    )
