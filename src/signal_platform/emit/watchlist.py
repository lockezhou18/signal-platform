"""Weekly watchlist emitter.

Writes the ranked top-N from ``pipeline.run_once`` to
``~/signal-platform-output/<date>.json`` and ``.md``. Every emit includes:

  - Status flag from the walk-forward validator
    (``validated`` / ``measurement-only`` / ``regime-alert``). Downstream
    consumers (the quant-advisor, Binghua, any future auto-action logic)
    MUST check this before trusting the ranking.
  - Explicit statement of the survivorship-bias caveat (see
    ``specs/universe-fetcher.md``).
  - Exact run config so a future re-run is reproducible.
  - The `composite_weights` trace so a reader can see which factors
    drove the ranking.

No auto-trading in this phase. The emit is a decision INPUT, not a
decision OUTPUT.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from signal_platform.logging import get_logger
from signal_platform.signals.walk_forward import WalkForwardStatus

logger = get_logger(__name__)

DEFAULT_OUTPUT_DIR = Path(os.path.expanduser("~")) / "signal-platform-output"


@dataclass(frozen=True)
class WatchlistPayload:
    """What a single weekly emit contains."""

    generated_at: str  # ISO-8601, UTC
    universe_name: str
    universe_size: int
    n_fetched: int
    horizon: int
    top_n: list[tuple[str, float]]
    status: WalkForwardStatus
    walkforward_aggregate: dict[str, float]
    composite_weights_top: list[tuple[str, float]]  # top factors by |weight|
    caveats: list[str]


def _render_markdown(payload: WatchlistPayload) -> str:
    """Human-readable view. Intentionally plain-text so it renders cleanly
    in a terminal, Obsidian, and GitHub."""
    lines: list[str] = []
    lines.append(f"# Watchlist — {payload.generated_at}\n")
    lines.append(f"**Status: `{payload.status.value}`**\n")

    if payload.status == WalkForwardStatus.REGIME_ALERT:
        lines.append("> ⚠ regime-alert: walk-forward validator failed or flipped sign.")
        lines.append("> Do not act on this ranking without additional analysis.\n")
    elif payload.status == WalkForwardStatus.MEASUREMENT_ONLY:
        lines.append("> ℹ measurement-only: Sharpe 0.5-0.8 in walk-forward.")
        lines.append("> Ranking has measurable signal but below the 'validated' threshold.\n")

    lines.append(
        f"**Universe:** `{payload.universe_name}` "
        f"({payload.n_fetched}/{payload.universe_size} symbols fetched)  "
        f"**Horizon:** {payload.horizon} trading days\n"
    )

    lines.append("## Top ranking\n")
    lines.append("| Rank | Symbol | Composite score |")
    lines.append("|-----:|:-------|---------------:|")
    for i, (sym, score) in enumerate(payload.top_n, start=1):
        lines.append(f"| {i} | `{sym}` | {score:+.3f} |")

    lines.append("")
    lines.append("## Walk-forward aggregate\n")
    for k, v in payload.walkforward_aggregate.items():
        lines.append(f"- **{k}:** {v}")

    lines.append("")
    lines.append("## Top factor weights (composite scorer trace)\n")
    lines.append("| Factor | Weight |")
    lines.append("|:-------|-------:|")
    for factor, weight in payload.composite_weights_top:
        lines.append(f"| `{factor}` | {weight:+.4f} |")

    lines.append("")
    lines.append("## Caveats\n")
    for caveat in payload.caveats:
        lines.append(f"- {caveat}")

    lines.append("")
    return "\n".join(lines)


def emit_watchlist(
    payload: WatchlistPayload,
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Write JSON + markdown emit files. Returns (json_path, md_path)."""
    target_dir = output_dir if output_dir is not None else DEFAULT_OUTPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    date_stamp = payload.generated_at[:10]  # YYYY-MM-DD from ISO
    json_path = target_dir / f"{date_stamp}.json"
    md_path = target_dir / f"{date_stamp}.md"

    json_payload = {
        "generated_at": payload.generated_at,
        "universe_name": payload.universe_name,
        "universe_size": payload.universe_size,
        "n_fetched": payload.n_fetched,
        "horizon": payload.horizon,
        "status": payload.status.value,
        "top_n": [{"symbol": s, "score": score} for s, score in payload.top_n],
        "walkforward_aggregate": payload.walkforward_aggregate,
        "composite_weights_top": [
            {"factor": f, "weight": w} for f, w in payload.composite_weights_top
        ],
        "caveats": payload.caveats,
    }

    json_path.write_text(json.dumps(json_payload, indent=2))
    md_path.write_text(_render_markdown(payload))

    logger.info(
        "watchlist_emitted",
        json_path=str(json_path),
        md_path=str(md_path),
        status=payload.status.value,
        top_n_count=len(payload.top_n),
    )
    return json_path, md_path


def build_payload(
    *,
    universe_name: str,
    universe_size: int,
    n_fetched: int,
    horizon: int,
    top_n: list[tuple[str, float]],
    status: WalkForwardStatus,
    walkforward_aggregate: dict[str, float],
    composite_weights: pd.DataFrame,
    top_factors: int = 10,
) -> WatchlistPayload:
    """Assemble a ``WatchlistPayload`` from pipeline outputs.

    Adds the standard caveats (survivorship bias, no auto-trading) that
    every emit must carry so a reader can't miss them.
    """
    top_weights = composite_weights["weight"].abs().sort_values(ascending=False).head(top_factors)
    composite_weights_top = [
        (str(factor), float(composite_weights.loc[factor, "weight"]))
        for factor in top_weights.index
    ]

    caveats = [
        "Universe uses CURRENT membership (snapshot). Survivorship bias is "
        "not corrected — documented in specs/universe-fetcher.md.",
        "Walk-forward top-decile backtest is a ranking-quality test, NOT a "
        "production strategy. Fees at 5bps/turn; no slippage modeling.",
        "This is a research input. No auto-trading is implemented. Always "
        "check status flag before acting.",
    ]

    return WatchlistPayload(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        universe_name=universe_name,
        universe_size=universe_size,
        n_fetched=n_fetched,
        horizon=horizon,
        top_n=top_n,
        status=status,
        walkforward_aggregate=walkforward_aggregate,
        composite_weights_top=composite_weights_top,
        caveats=caveats,
    )
