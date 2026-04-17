# Tasks: signal-platform-p1

Ordered for **incremental verification** (Principle V). Each task is independently testable; CI must stay green throughout.

## PR 1 — Foundations (universe + factors + observability scaffold)

- [ ] T01 Universe fetcher: S&P 500 scrape + hardcoded snapshot fallback
- [ ] T02 OHLCV fetcher with parquet cache + parallel thread pool
- [ ] T03 Copy factor library from `financial-engine/trade/factors.py` into `src/signal_platform/factors/`
- [ ] T04 Unit tests for universe + factors (using cached fixtures)
- [ ] T05 Structured logging (JSON, structlog) — log every data-fetch event with symbol + outcome
- [ ] T06 Prometheus metrics skeleton — register heartbeat + empty per-subsystem stubs
- [ ] T07 `/healthz` + `/metrics` HTTP server on :9095 (stdlib http.server, no flask)
- [ ] T08 Service wrapper: long-running process with internal scheduler (APScheduler or stdlib Timer)
- [ ] T09 CI green on all of the above

**Gate:** service starts, `/metrics` responds with heartbeat, `/healthz` green.

## PR 2 — Signal engine

- [ ] T10 Cross-sectional IC engine per `specs/factor-ic-engine.md`
- [ ] T11 Unit tests: synthetic perfect/shuffled/partial-coverage
- [ ] T12 Composite scorer (equal-weight baseline) per `specs/composite-scorer.md`
- [ ] T13 Composite scorer (Grinold residualized)
- [ ] T14 Unit tests for residualization — correlated-factor test case from spec
- [ ] T15 Wire IC + composite into the scheduled run; emit `/metrics` updates per factor
- [ ] T16 CI green

**Gate:** scheduled run produces IC summary + composite score for S&P 500 universe; metrics reflect it; manual inspection of weights looks sensible (momentum cluster doesn't dominate at 100%).

## PR 3 — Validation + emit + tenant registration

- [ ] T17 Walk-forward validator per `specs/walk-forward-validator.md`
- [ ] T18 Unit tests: synthetic perfect/random
- [ ] T19 Status flag logic + metrics
- [ ] T20 Weekly watchlist emit — JSON + markdown to `~/signal-platform-output/`
- [ ] T21 Runbook at `docs/runbooks/signal-platform.md` per observability contract
- [ ] T22 `platform.yaml` tenant manifest at repo root
- [ ] T23 `obs validate platform.yaml` passes
- [ ] T24 Deployment artifact: systemd unit OR docker-compose.yml (decide during BUILD) committed to `deployment/`
- [ ] T25 First live run on mac-mini; `obs register`; verify Grafana shows tenant
- [ ] T26 CI green

**Gate:** signal-platform is a registered observability tenant; first weekly watchlist produced; status flag correctly computed.

## Optional PR 4 — Polish (if bandwidth)

- [ ] T27 QQQ-100 universe in addition to S&P 500
- [ ] T28 Dashboard custom panels (factor IC heatmap, weekly watchlist table)
- [ ] T29 README update with real outputs + sample watchlist screenshot
- [ ] T30 Archive this OpenSpec change via `openspec archive signal-platform-p1`

## Cross-cutting requirements

Every PR:
- Ruff + ruff-format clean
- Mypy strict clean
- Pytest green (no xfail without explicit comment)
- CI pipeline green on Gitea
- Dual-pushed to GitHub + Gitea
- Commit messages reference the task ID (`feat(T10): cross-sectional IC engine`)

## Explicitly NOT in this change

- Fundamentals data (P2)
- Insider buying (P3)
- Earnings surprises (P4)
- Narrative scoring (P5)
- Options flow (P6)
- Macro regime (P7)
- Order execution integration with `financial-engine` — read-only
- Multi-user / RBAC
- Real-time data
- LLM-based explanations for why a name is ranked high
