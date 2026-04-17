"""Metrics module smoke tests."""
from __future__ import annotations

import time
import urllib.request

import pytest

from signal_platform import metrics


@pytest.fixture(autouse=True)
def _stop_server_after() -> None:
    yield
    metrics.stop_metrics_server()


def test_metrics_server_starts_and_serves_metrics() -> None:
    server = metrics.start_metrics_server(port=19095)
    assert server is not None

    # Bump a metric so /metrics output is non-trivial
    metrics.heartbeat_timestamp.set_to_current_time()
    metrics.universe_size.set(500)

    time.sleep(0.1)

    with urllib.request.urlopen("http://127.0.0.1:19095/metrics") as resp:
        body = resp.read().decode()

    assert resp.status == 200
    assert "signal_platform_heartbeat_timestamp" in body
    assert "signal_platform_universe_size 500.0" in body


def test_healthz_returns_json_ok() -> None:
    metrics.start_metrics_server(port=19096)
    metrics.heartbeat_timestamp.set_to_current_time()

    with urllib.request.urlopen("http://127.0.0.1:19096/healthz") as resp:
        body = resp.read().decode()

    assert resp.status == 200
    assert '"status": "ok"' in body
    assert '"service": "signal-platform"' in body


def test_metrics_server_is_idempotent() -> None:
    first = metrics.start_metrics_server(port=19097)
    second = metrics.start_metrics_server(port=19097)
    assert first is second
