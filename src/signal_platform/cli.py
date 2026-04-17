"""CLI entrypoints for ad-hoc runs (separate from the long-running service)."""
from __future__ import annotations

import sys

import click

from signal_platform import __version__
from signal_platform.logging import bind_run_context, configure_logging, get_logger

logger = get_logger(__name__)


@click.group()
@click.version_option(__version__)
def main() -> None:
    """signal-platform — multi-signal quantitative stock screener."""
    configure_logging()
    bind_run_context()


@main.command()
def status() -> None:
    """Report service status (placeholder — real impl in T08)."""
    click.echo(f"signal-platform v{__version__}")
    click.echo("status: scaffold only (signal engine lands in PR 2)")


@main.command()
def service() -> None:
    """Run the long-running service (same as `python -m signal_platform.service`)."""
    from signal_platform.service import run

    sys.exit(run())


if __name__ == "__main__":
    main()
