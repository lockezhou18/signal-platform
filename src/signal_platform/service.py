"""Long-running service wrapper.

Starts the metrics HTTP server, runs a scheduler loop that updates the
heartbeat timestamp every 30 seconds, and (in future phases) triggers
weekly pipeline runs. For P1 this is the skeleton; actual fetch/score/
walk-forward integration lands in subsequent tasks per
`openspec/changes/signal-platform-p1/tasks.md`.
"""

from __future__ import annotations

import signal
import sys
import threading

from signal_platform import metrics
from signal_platform.logging import bind_run_context, configure_logging, get_logger

logger = get_logger(__name__)

# Heartbeat cadence. MUST stay strictly less than
# ``platform.yaml::probes[heartbeat].max_age_seconds`` or the observability
# platform will flap the probe. Current tenant manifest: max_age=120, so
# 30s gives us a 4× margin. If you change either, change both.
HEARTBEAT_INTERVAL_SECONDS = 30

_shutdown_event = threading.Event()


def _handle_signal(signum: int, _frame: object) -> None:
    logger.info("shutdown_signal_received", signum=signum)
    _shutdown_event.set()


def run() -> int:
    """Main service entrypoint. Returns 0 on clean shutdown, non-zero on fatal.

    Uses ``threading.Event.wait`` instead of ``time.sleep`` so SIGTERM /
    SIGINT can interrupt the heartbeat cadence without waiting for the full
    sleep interval to elapse.
    """
    configure_logging()
    run_id = bind_run_context()
    logger.info("service_starting", run_id=run_id)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    metrics.start_metrics_server()

    # Initial heartbeat so the /metrics endpoint has a non-zero value immediately
    metrics.set_heartbeat()

    logger.info("service_ready", metrics_port=9095)

    while not _shutdown_event.is_set():
        metrics.set_heartbeat()
        # NOTE: scheduler for weekly pipeline runs lands in T08; this loop
        # currently only maintains liveness for the observability platform.
        _shutdown_event.wait(timeout=HEARTBEAT_INTERVAL_SECONDS)

    metrics.stop_metrics_server()
    logger.info("service_stopped")
    return 0


if __name__ == "__main__":
    sys.exit(run())
