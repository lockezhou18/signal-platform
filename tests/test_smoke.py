"""Smoke tests — CI's canary.

Only keeps tests that validate something more than a literal restatement
of code. The tautological version-equality test was removed per review.
"""

from __future__ import annotations


def test_package_imports_and_exports_version() -> None:
    """Package loads cleanly and exposes a semver-shaped __version__."""
    import signal_platform

    assert isinstance(signal_platform.__version__, str)
    parts = signal_platform.__version__.split(".")
    assert len(parts) == 3, f"expected semver major.minor.patch, got {signal_platform.__version__}"
    assert all(p.isdigit() for p in parts), (
        f"non-numeric version part: {signal_platform.__version__}"
    )


def test_public_modules_importable() -> None:
    """Core modules import without side-effects that would crash at load time."""
    import signal_platform.logging  # noqa: F401
    import signal_platform.metrics  # noqa: F401
