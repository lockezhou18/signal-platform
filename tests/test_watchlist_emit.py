"""Watchlist emit tests — writes to a tmp dir; no real filesystem side effects."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from signal_platform.emit.watchlist import build_payload, emit_watchlist
from signal_platform.signals.walk_forward import WalkForwardStatus


def _sample_payload(status: WalkForwardStatus = WalkForwardStatus.VALIDATED):
    weights = pd.DataFrame(
        {"weight": [0.3, 0.2, 0.1, 0.4], "residual_ic": [0.04, 0.03, 0.01, 0.05]},
        index=["roc_30", "vol_std_10", "bb_pos_30", "macd_signal"],
    )
    return build_payload(
        universe_name="mega",
        universe_size=15,
        n_fetched=15,
        horizon=5,
        top_n=[("AMD", 1.2), ("ASML", 1.1), ("AMZN", 0.4)],
        status=status,
        walkforward_aggregate={"mean_sharpe": 1.2, "n_windows": 14.0},
        composite_weights=weights,
        top_factors=3,
    )


def test_emit_writes_json_and_markdown(tmp_path: Path) -> None:
    payload = _sample_payload()
    json_path, md_path = emit_watchlist(payload, output_dir=tmp_path)
    assert json_path.exists()
    assert md_path.exists()

    data = json.loads(json_path.read_text())
    assert data["universe_name"] == "mega"
    assert data["status"] == "validated"
    assert len(data["top_n"]) == 3
    assert data["top_n"][0] == {"symbol": "AMD", "score": 1.2}


def test_emit_markdown_contains_status_and_rankings(tmp_path: Path) -> None:
    payload = _sample_payload()
    _, md_path = emit_watchlist(payload, output_dir=tmp_path)
    md = md_path.read_text()
    assert "`validated`" in md
    assert "AMD" in md
    assert "ASML" in md
    assert "Caveats" in md


def test_emit_regime_alert_includes_warning(tmp_path: Path) -> None:
    payload = _sample_payload(status=WalkForwardStatus.REGIME_ALERT)
    _, md_path = emit_watchlist(payload, output_dir=tmp_path)
    md = md_path.read_text()
    assert "regime-alert" in md
    assert "Do not act on this ranking" in md


def test_emit_measurement_only_includes_note(tmp_path: Path) -> None:
    payload = _sample_payload(status=WalkForwardStatus.MEASUREMENT_ONLY)
    _, md_path = emit_watchlist(payload, output_dir=tmp_path)
    md = md_path.read_text()
    assert "measurement-only" in md
    assert "below the 'validated' threshold" in md


def test_emit_uses_date_stamped_filenames(tmp_path: Path) -> None:
    payload = _sample_payload()
    json_path, md_path = emit_watchlist(payload, output_dir=tmp_path)
    # YYYY-MM-DD.json, YYYY-MM-DD.md
    assert json_path.name.endswith(".json")
    assert md_path.name.endswith(".md")
    date_stem = json_path.stem
    assert len(date_stem) == 10
    assert date_stem[4] == "-" and date_stem[7] == "-"
    # Both files share the same stem (same date)
    assert json_path.stem == md_path.stem


def test_build_payload_includes_survivorship_bias_caveat() -> None:
    payload = _sample_payload()
    caveats_blob = " ".join(payload.caveats)
    assert "survivorship" in caveats_blob.lower() or "Survivorship" in caveats_blob
    assert "auto-trading" in caveats_blob.lower() or "auto-trad" in caveats_blob.lower()


def test_build_payload_picks_top_factors_by_absolute_weight() -> None:
    payload = _sample_payload()
    # Largest |weight| was macd_signal (0.4), then roc_30 (0.3), vol_std_10 (0.2)
    assert payload.composite_weights_top[0][0] == "macd_signal"
    assert payload.composite_weights_top[1][0] == "roc_30"
