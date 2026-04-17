"""Smoke tests — CI's canary."""
import signal_platform


def test_version() -> None:
    assert signal_platform.__version__ == "0.1.0"


def test_imports_clean() -> None:
    from signal_platform import __version__
    assert isinstance(__version__, str)
