# signal-platform

Multi-signal quantitative stock screener + thesis research platform.

## Vision — Two tracks

| Track | Purpose | Operator |
|---|---|---|
| **A. Quantitative screener** | Rank a broad universe by orthogonal factor signals (Grinold-residualized composite). Surface top-N candidates weekly. | Mechanical — IC-gated per phase |
| **B. Thesis research** | Maintain sector/theme watchlists grounded in first-principles industry understanding (AI infra, memory, power, networking, etc.) | Human-in-loop via `/research` workflow |

Each track reinforces the other: Track B generates the universe; Track A scores it. Corroborated names are the high-conviction candidates.

## Status

PR 1–3 shipped. Service is deployed (launchd agent on mac-mini, Prometheus health=up, Grafana tenant registered).

- **Phase 1 of 7 per** `openspec/changes/signal-platform-p1/`
- Full roadmap: P2 fundamentals → P3 insider → P4 earnings → P5 narrative → P6 options → P7 macro regime → integration

## Example live run (wheel universe, 5y)

```
$ signal-platform run-once --universe wheel --emit
universe=wheel fetched=4/4
status=validated  mean_sharpe=0.823  n_windows=11
  1. MSFT +0.312
  2. IWM  +0.155
  3. QQQ  -0.039
  4. SPY  -0.428
emitted:
  ~/signal-platform-output/2026-04-17.json
  ~/signal-platform-output/2026-04-17.md
```

## Repository

- **GitHub (primary)**: `lockezhou18/signal-platform`
- **Gitea (CI)**: `bighua/signal-platform` at `http://192.168.0.102:3000` — dual-push; `mac-mini-runner` runs ruff + format + mypy strict + pytest on every push.

## Observability

Registered as tenant with `lockezhou18/observability`. See `platform.yaml`. Exposes Prometheus metrics on `:9095/metrics` + JSON `:9095/healthz`. Heartbeat + e2e + walk-forward-regime probes.

Known platform gotcha: `obs register` writes `localhost:<port>` into the Prometheus target file. When Prometheus runs in Docker, `localhost` resolves to the container — patch the target file to `host.docker.internal:<port>` (macOS Docker Desktop). Upstream fix belongs in the observability platform's registration CLI.

## Quickstart

```bash
# Local dev
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# Gates
ruff check src/ tests/
ruff format --check src/ tests/
mypy
pytest

# Run the service (heartbeat loop; exposes /metrics + /healthz on :9095)
python -m signal_platform.service

# Ad-hoc full-pipeline run + write watchlist to ~/signal-platform-output/
signal-platform run-once --universe mega --horizon 5 --top-n 10 --emit
```

## Supported universes (P1)

| Name    | Size | Source |
|---------|------|--------|
| `sp500` | ~40 | committed snapshot `src/signal_platform/data/sp500_snapshot.txt` (Wikipedia scrape deferred) |
| `mega`  | 15  | top 15 from `sp500` snapshot |
| `qqq`   | ~24 | explicit Nasdaq-100 subset (heavy AI/tech) |
| `wheel` | 4   | SPY / QQQ / IWM / MSFT (matches the wheel strategy in `financial-engine`) |

Survivorship bias uncorrected — documented in every emit and in `specs/universe-fetcher.md`.

## Deployment

macOS launchd agent — `deployment/launchd/com.openclaw.signal-platform.plist`. Install:

```bash
cp deployment/launchd/com.openclaw.signal-platform.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.openclaw.signal-platform.plist
curl http://localhost:9095/healthz   # should return {"status":"ok",...}
```

Logs at `~/signal-platform-logs/service.{log,err}`.

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
- Status flags: every watchlist emit carries `validated` / `measurement-only` / `regime-alert` — consumers MUST check before acting
