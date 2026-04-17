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
    """Report service status."""
    click.echo(f"signal-platform v{__version__}")
    click.echo("status: PR 2 shipped (IC engine + Grinold scorer)")


@main.command()
def service() -> None:
    """Run the long-running service (same as `python -m signal_platform.service`)."""
    from signal_platform.service import run

    sys.exit(run())


@main.command("run-once")
@click.option("--universe", default="sp500", help="Universe alias (sp500 / mega / wheel).")
@click.option("--horizon", default=5, type=int, help="Forward-return horizon in trading days.")
@click.option("--top-n", default=20, type=int, help="Number of names to emit in the ranking.")
@click.option(
    "--lookback-windows",
    default=52,
    type=int,
    help="Rebalance windows to consider for IC residualization.",
)
@click.option(
    "--walkforward/--no-walkforward",
    default=True,
    help="Run walk-forward validator (slower, but gives status flag).",
)
@click.option(
    "--emit/--no-emit",
    default=False,
    help="Write watchlist JSON + markdown to ~/signal-platform-output/",
)
def run_once_cmd(
    universe: str,
    horizon: int,
    top_n: int,
    lookback_windows: int,
    walkforward: bool,
    emit: bool,
) -> None:
    """Run the full screener pipeline once: universe → fetch → IC → score → top-N.

    Prints the ranked watchlist to stdout. With --emit, also writes JSON +
    markdown to ~/signal-platform-output/<date>.{json,md} annotated with the
    walk-forward status flag. Requires network for the yfinance fetch stage.
    """
    from signal_platform.pipeline import run_once

    result = run_once(
        universe_name=universe,
        horizon=horizon,
        lookback_windows=lookback_windows,
        top_n=top_n,
        run_walkforward=walkforward,
        emit=emit,
    )

    click.echo(f"universe={universe} fetched={result.n_fetched}/{len(result.universe)}")
    click.echo(f"horizon={result.horizon}d  top-{top_n}:")
    if walkforward:
        click.echo(f"status={result.walkforward_status.value}  agg={result.walkforward_aggregate}")
    click.echo("")
    for i, (sym, score) in enumerate(result.top_n, start=1):
        click.echo(f"  {i:>2}. {sym:<6}  score={score:+.3f}")
    if result.emit_paths is not None:
        click.echo(f"\nemitted:\n  {result.emit_paths[0]}\n  {result.emit_paths[1]}")


if __name__ == "__main__":
    main()
