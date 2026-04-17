"""Metrics module smoke tests."""

from __future__ import annotations

import json
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

    # Use the public helper (set_heartbeat) instead of the private gauge
    # so this test exercises the single supported update path.
    before = time.time()
    metrics.set_heartbeat()
    metrics.universe_size.set(500)

    with urllib.request.urlopen("http://127.0.0.1:19095/metrics") as resp:
        body = resp.read().decode()

    assert resp.status == 200
    assert "platform_heartbeat_timestamp" in body
    assert "signal_platform_universe_size 500.0" in body
    # Heartbeat value should be a recent timestamp (within this test's window).
    assert metrics.get_last_heartbeat_ts() >= before


def test_healthz_returns_json_ok_with_live_heartbeat() -> None:
    metrics.start_metrics_server(port=19096)
    before = time.time()
    metrics.set_heartbeat()

    with urllib.request.urlopen("http://127.0.0.1:19096/healthz") as resp:
        body = resp.read().decode()

    assert resp.status == 200
    payload = json.loads(body)
    assert payload["status"] == "ok"
    assert payload["service"] == "signal-platform"
    # Heartbeat must be reflected in the JSON, not just the Prometheus gauge.
    # This is the behavior the set_heartbeat() helper exists to guarantee.
    assert payload["heartbeat_ts"] >= before, (
        "healthz must expose the timestamp written by set_heartbeat(); "
        "a zero here means the gauge was updated but the tracker wasn't."
    )


def test_set_heartbeat_accepts_explicit_timestamp() -> None:
    """Passing an explicit ts overrides time.time() and updates both gauge+tracker."""
    metrics.set_heartbeat(ts=1000000.0)
    assert metrics.get_last_heartbeat_ts() == 1000000.0


def test_metrics_server_is_idempotent() -> None:
    first = metrics.start_metrics_server(port=19097)
    second = metrics.start_metrics_server(port=19097)
    assert first is second
