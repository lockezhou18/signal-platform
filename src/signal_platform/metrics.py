"""Prometheus metrics registry + minimal HTTP server.

Exposes `:9095/metrics` in Prometheus text format and `:9095/healthz` as
JSON. No Flask/FastAPI — stdlib http.server is sufficient for this
scrape-only workload.

See `openspec/changes/signal-platform-p1/specs/observability-contract.md`
for the complete metric set this module must expose.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from signal_platform.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    # Registry + contracted gauges
    "REGISTRY",
    "e2e_probe_ok",
    "walkforward_status",
    # Subsystem metrics
    "cache_hit_total",
    "composite_weight",
    "dropped_factor_total",
    "factor_ic",
    "factor_ic_mean",
    "ic_low_coverage_total",
    "last_run_duration_seconds",
    "last_run_status",
    "universe_size",
    "yfinance_errors_total",
    # Heartbeat helpers (public write path)
    "get_last_heartbeat_ts",
    "set_heartbeat",
    # Server
    "start_metrics_server",
    "stop_metrics_server",
]

REGISTRY = CollectorRegistry()

# --- Contracted metrics (observability platform schema v1) ---
# Names follow the 'platform_*' prefix convention enforced by
# obs/schema/platform-v1.json. These are the metrics the platform SCRAPES;
# tenant-internal metrics below use 'signal_platform_*' prefix.
#
# `_heartbeat_timestamp` is deliberately private: the public path is
# `set_heartbeat()`, which keeps the Prometheus gauge and the `/healthz`
# `_last_heartbeat_ts` tracker in lockstep. Callers that touch the gauge
# directly would make `/healthz` lie about heartbeat freshness.

_heartbeat_timestamp = Gauge(
    "platform_heartbeat_timestamp",
    "Unix timestamp of the most recent scheduler tick.",
    registry=REGISTRY,
)

e2e_probe_ok = Gauge(
    "platform_signal_platform_probe_ok",
    "1 if the full pipeline (fetch->IC->score->walkforward->emit) completed within last 7 days, else 0.",
    registry=REGISTRY,
)

walkforward_status = Gauge(
    "platform_signal_platform_walkforward_status",
    "Walk-forward validator status. 0=regime-alert, 1=measurement-only, 2=validated.",
    registry=REGISTRY,
)

# --- Subsystem run status ---

last_run_status = Gauge(
    "signal_platform_last_run_status",
    "1 if last run of subsystem succeeded, 0 otherwise.",
    labelnames=("run_type",),
    registry=REGISTRY,
)

last_run_duration_seconds = Gauge(
    "signal_platform_last_run_duration_seconds",
    "Duration (seconds) of the most recent run of each subsystem.",
    labelnames=("run_type",),
    registry=REGISTRY,
)

# --- Data layer ---

universe_size = Gauge(
    "signal_platform_universe_size",
    "Number of symbols in the most recent universe fetch.",
    registry=REGISTRY,
)

yfinance_errors_total = Counter(
    "signal_platform_yfinance_errors_total",
    "yfinance fetch errors by category.",
    labelnames=("error_type",),
    registry=REGISTRY,
)

cache_hit_total = Counter(
    "signal_platform_cache_hit_total",
    "OHLCV parquet cache outcomes.",
    labelnames=("outcome",),
    registry=REGISTRY,
)

# --- Signal layer ---

factor_ic = Histogram(
    "signal_platform_factor_ic",
    "Cross-sectional IC distribution per factor+horizon.",
    labelnames=("factor", "horizon"),
    buckets=(-0.3, -0.2, -0.1, -0.05, -0.02, 0.0, 0.02, 0.05, 0.1, 0.2, 0.3),
    registry=REGISTRY,
)

factor_ic_mean = Gauge(
    "signal_platform_factor_ic_mean",
    "Trailing-12w mean IC per factor+horizon.",
    labelnames=("factor", "horizon"),
    registry=REGISTRY,
)

composite_weight = Gauge(
    "signal_platform_composite_weight",
    "Current Grinold-residualized weight per factor.",
    labelnames=("factor",),
    registry=REGISTRY,
)

dropped_factor_total = Counter(
    "signal_platform_dropped_factor_total",
    "Factors dropped from scoring by reason.",
    labelnames=("reason",),
    registry=REGISTRY,
)

ic_low_coverage_total = Counter(
    "signal_platform_ic_low_coverage_total",
    "IC windows rejected for insufficient symbol coverage, by horizon.",
    labelnames=("horizon",),
    registry=REGISTRY,
)


# --- Module-level state (avoids private-API access on prometheus_client) ---

_START_TIME = time.time()
_last_heartbeat_ts: float = 0.0
_server: ThreadingHTTPServer | None = None
_server_thread: threading.Thread | None = None
_server_lock = threading.Lock()


def set_heartbeat(ts: float | None = None) -> float:
    """Update the heartbeat gauge AND the healthz tracker in lockstep.

    This is the ONLY supported path for updating the heartbeat.
    Calling ``_heartbeat_timestamp.set(...)`` directly would update the
    Prometheus gauge but leave ``/healthz`` reporting a stale timestamp.
    """
    global _last_heartbeat_ts
    resolved = ts if ts is not None else time.time()
    _heartbeat_timestamp.set(resolved)
    _last_heartbeat_ts = resolved
    return resolved


def get_last_heartbeat_ts() -> float:
    """Read the most recently set heartbeat timestamp (for tests + diagnostics)."""
    return _last_heartbeat_ts


class _Handler(BaseHTTPRequestHandler):
    """Minimal metrics + healthz handler."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — stdlib signature
        return

    def do_GET(self) -> None:  # noqa: N802 — stdlib contract
        if self.path == "/metrics":
            body = generate_latest(REGISTRY)
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/healthz":
            payload = json.dumps(
                {
                    "status": "ok",
                    "service": "signal-platform",
                    "heartbeat_ts": _last_heartbeat_ts,
                    "uptime_s": int(time.time() - _START_TIME),
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


def start_metrics_server(port: int | None = None) -> ThreadingHTTPServer:
    """Start the metrics server on a background thread.

    Idempotent: subsequent calls with the already-running server return the
    existing instance. Port defaults to SIGNAL_PLATFORM_METRICS_PORT env or 9095.
    Thread-safe via ``_server_lock``.
    """
    global _server, _server_thread

    with _server_lock:
        if _server is not None:
            return _server

        resolved_port = (
            port if port is not None else int(os.getenv("SIGNAL_PLATFORM_METRICS_PORT", "9095"))
        )
        _server = ThreadingHTTPServer(("0.0.0.0", resolved_port), _Handler)
        _server_thread = threading.Thread(
            target=_server.serve_forever,
            name="signal-platform-metrics",
            daemon=True,
        )
        _server_thread.start()
        logger.info("metrics_server_started", port=resolved_port)
        return _server


def stop_metrics_server() -> None:
    """Stop the metrics server if running. Safe to call multiple times."""
    global _server, _server_thread
    with _server_lock:
        if _server is not None:
            _server.shutdown()
            _server.server_close()
            _server = None
        if _server_thread is not None:
            _server_thread.join(timeout=5)
            _server_thread = None
