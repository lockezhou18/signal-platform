"""Universe selection — which tickers to screen.

P1 ships with a committed snapshot of ~40 large-cap names (SPY/QQQ/IWM/DIA +
mega-caps + sector representatives). Full S&P 500 membership via Wikipedia
scrape is deferred to a later phase (see `openspec/changes/signal-platform-p1/
specs/universe-fetcher.md` for the limitation).

Survivorship-bias warning: we use current membership, not point-in-time. This
limitation is documented in every watchlist emit.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from signal_platform.logging import get_logger

logger = get_logger(__name__)

_SNAPSHOT_PACKAGE = "signal_platform.data"
_SNAPSHOT_FILE = "sp500_snapshot.txt"

# Universes recognised by get_universe(). Names map to snapshot-derived lists;
# more specialised universes (sector slices, QQQ-100 union, etc.) added in
# later phases as the need appears.
_ALIAS_TO_SLICE: dict[str, slice | None] = {
    "sp500": None,  # full snapshot
    "mega": slice(0, 15),  # top 15 from snapshot = core index + mega-cap tech
    "wheel": None,  # placeholder; resolved by explicit list below
}

_WHEEL_EXPLICIT = ["SPY", "QQQ", "IWM", "MSFT"]


def _load_snapshot() -> list[str]:
    """Read the committed ticker list, strip comments + blank lines, sort + dedup."""
    try:
        raw = resources.files(_SNAPSHOT_PACKAGE).joinpath(_SNAPSHOT_FILE).read_text()
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        # Fallback for source-tree runs that bypass importlib.resources.
        source_path = Path(__file__).parent / _SNAPSHOT_FILE
        if not source_path.exists():
            raise RuntimeError(f"universe snapshot not found: {source_path}") from exc
        raw = source_path.read_text()

    tickers = [
        line.strip()
        for line in raw.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return sorted(set(tickers))


def get_universe(name: str = "sp500") -> list[str]:
    """Return sorted list of tickers for the requested universe.

    Supported names:
      - "sp500": full committed snapshot (~40 names in P1)
      - "mega": top 15 mega-caps (fast iteration / smoke tests)
      - "wheel": explicit SPY/QQQ/IWM/MSFT set

    Unknown names raise ValueError; no silent fallback.
    """
    normalized = name.lower()

    if normalized == "wheel":
        logger.info("universe_loaded", name=normalized, size=len(_WHEEL_EXPLICIT))
        return sorted(_WHEEL_EXPLICIT)

    if normalized not in _ALIAS_TO_SLICE:
        raise ValueError(
            f"unknown universe '{name}'. Known: {sorted(_ALIAS_TO_SLICE.keys())}"
        )

    snapshot = _load_snapshot()
    sel = _ALIAS_TO_SLICE[normalized]
    tickers = snapshot if sel is None else snapshot[sel]
    logger.info("universe_loaded", name=normalized, size=len(tickers))
    return tickers
