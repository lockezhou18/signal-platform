"""Service-level tests — SIGTERM handling, heartbeat loop.

Runs the service as a subprocess, sends SIGTERM, asserts clean exit.
This is the only way to meaningfully test signal handling; mocking
signal.signal would test mock behavior, not the real shutdown path.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _healthz_ready(port: int, timeout_s: float = 5.0) -> bool:
    """Poll /healthz until it responds 200 or timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=0.5) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.1)
    return False


def test_service_responds_then_terminates_on_sigterm() -> None:
    """Service starts, serves /healthz, exits 0 within 5s of SIGTERM."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "signal_platform.service"],
        cwd=str(REPO_ROOT),
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
            "HOME": os.path.expanduser("~"),
            "SIGNAL_PLATFORM_METRICS_PORT": "19098",
            "PYTHONPATH": str(REPO_ROOT / "src"),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        assert _healthz_ready(19098, timeout_s=5.0), "service did not come up on :19098"

        proc.send_signal(signal.SIGTERM)
        exit_code = proc.wait(timeout=5)
        assert exit_code == 0, f"expected clean exit (0), got {exit_code}"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
