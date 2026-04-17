# signal-platform

Multi-signal quantitative stock screener + thesis research platform.

## Vision — Two tracks

| Track | Purpose | Operator |
|---|---|---|
| **A. Quantitative screener** | Rank a broad universe by orthogonal factor signals (Grinold-residualized composite). Surface top-N candidates weekly. | Mechanical — IC-gated per phase |
| **B. Thesis research** | Maintain sector/theme watchlists grounded in first-principles industry understanding (AI infra, memory, power, networking, etc.) | Human-in-loop via `/research` workflow |

Each track reinforces the other: Track B generates the universe; Track A scores it. Corroborated names are the high-conviction candidates.

## Status

**Stage:** BUILD (Phase 1 of 7) per `/lfg` pipeline.

- `openspec/changes/signal-platform-p1/` — current phase (cross-sectional IC on price-volume factors)
- Full roadmap: P2 fundamentals → P3 insider → P4 earnings → P5 narrative → P6 options → P7 macro regime → Integration

## Repository

- **GitHub (primary)**: `lockezhou18/signal-platform`
- **Gitea (CI)**: `bighua/signal-platform` at `http://192.168.0.102:3000` — dual-push configured; pushes trigger `mac-mini-runner`

## Observability

Registered as tenant with `lockezhou18/observability` platform. See `platform.yaml`. Exposes Prometheus metrics on `:9095/metrics`; heartbeat + e2e probe.

## Quickstart

```bash
uv sync                              # or: pip install -e '.[dev]'
ruff check src/ tests/
mypy
pytest

# Run the service (exposes /metrics, runs internal scheduler)
python -m signal_platform.service

# Ad-hoc IC run
python -m signal_platform.cli ic --universe sp500 --period 5y
```

## Foundational KB

This project is built on reasoning codified in:
- `team-shared/knowledge/notes/note-20260416-120000-alpha-combination-vs-israelov.md` — Grinold ↔ Israelov synthesis
- `team-shared/knowledge/notes/note-20260411-100000-hull-ch12-options-cashflow-taxonomy.md` — options-income taxonomy + CBOE empirics
- `team-shared/knowledge/sources/alpha-combination-20260416/rohonchain-math-behind-50-weak-signals.md` — the alpha-combination framework

Contributors should read these before proposing new signals or scoring changes.

## Engineering principles

Per `team-shared/engineering-principles.md`:
- Every spec includes failure modes
- Every spec includes observability requirements
- Tasks ordered for incremental verification
- Walk-forward validation mandatory before any "validated" output
- IC-gated expansion: no new signal family added without measured residual IC ≥ 0.02
