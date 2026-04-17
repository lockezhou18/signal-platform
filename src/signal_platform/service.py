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
import time

from signal_platform import metrics
from signal_platform.logging import bind_run_context, configure_logging, get_logger

logger = get_logger(__name__)

_shutdown = False


def _handle_signal(signum: int, _frame: object) -> None:
    global _shutdown
    logger.info("shutdown_signal_received", signum=signum)
    _shutdown = True


def run() -> int:
    """Main service entrypoint. Returns 0 on clean shutdown, non-zero on fatal."""
    configure_logging()
    run_id = bind_run_context()
    logger.info("service_starting", run_id=run_id)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    metrics.start_metrics_server()

    # Initial heartbeat so the /metrics endpoint has a non-zero value immediately
    metrics.heartbeat_timestamp.set_to_current_time()

    logger.info("service_ready", metrics_port=9095)

    while not _shutdown:
        metrics.heartbeat_timestamp.set_to_current_time()
        # NOTE: scheduler for weekly pipeline runs lands in T08; this loop
        # currently only maintains liveness for the observability platform.
        time.sleep(30)

    metrics.stop_metrics_server()
    logger.info("service_stopped")
    return 0


if __name__ == "__main__":
    sys.exit(run())
