# Design: signal-platform-p1

## Architecture

```
                                    ┌─────────────────────────────────────────┐
                                    │ signal_platform service (long-running)  │
                                    │                                         │
  yfinance ────► universe/           │  ┌───────────────┐  ┌───────────────┐  │
                 ohlcv fetch         │  │ scheduler     │  │ metrics :9095 │──┼─► Prometheus
                 (cached parquet) ──►│  │ (weekly Sun)  │  │ (stdlib HTTP) │  │   (obs tenant)
                                    │  └──────┬────────┘  └───────────────┘  │
                                    │         │                              │
                                    │         ▼                              │
                                    │  ┌──────────────────────────────────┐  │
                                    │  │ ic_engine.compute_cross_sectional │  │
                                    │  │ - factors (existing 45 from      │  │
                                    │  │   financial-engine/trade/        │  │
                                    │  │   factors.py — copied into src/) │  │
                                    │  │ - forward returns 1d/5d/20d      │  │
                                    │  │ - Spearman rank IC per (factor,  │  │
                                    │  │   horizon, timestamp)            │  │
                                    │  └──────┬──────────────────────────┘  │
                                    │         ▼                              │
                                    │  ┌────────────────────────────────┐   │
                                    │  │ scorer.composite_grinold       │   │
                                    │  │ - Grinold residualization      │   │
                                    │  │   (step 9)                     │   │
                                    │  │ - equal-weight comparison      │   │
                                    │  │ - output: ranked cross-section │   │
                                    │  └──────┬─────────────────────────┘   │
                                    │         ▼                              │
                                    │  ┌────────────────────────────────┐   │
                                    │  │ walk_forward.validate_topk     │   │
                                    │  │ - top-decile weekly rebalance  │   │
                                    │  │ - OOS Sharpe across 10+ wins   │   │
                                    │  │ - status flag determination    │   │
                                    │  └──────┬─────────────────────────┘   │
                                    │         ▼                              │
                                    │  ┌────────────────────────────────┐   │
                                    │  │ emit.weekly_watchlist          │   │
                                    │  │ - top-20 names + status        │   │
                                    │  │ - JSON + markdown to output/   │   │
                                    │  └────────────────────────────────┘   │
                                    └─────────────────────────────────────────┘
```

## State management (Principle III)

| State | Location | Lifetime | Recovery |
|---|---|---|---|
| OHLCV cache | `~/signal-platform-data/ohlcv/<sym>.parquet` | rolling 5y, daily updated | re-fetch from yfinance if missing; idempotent |
| IC history | `~/signal-platform-data/ic-history.parquet` | append-only, per run | regenerable from OHLCV; not load-bearing |
| Last successful run timestamp | `~/signal-platform-data/state.json` | single value, rewritten | defaults to epoch if missing |
| Watchlist output | `~/signal-platform-output/<date>.json` + `.md` | kept indefinitely | immutable once written |
| Prometheus metrics | in-memory, last-value | lifetime of process | scraped by Prometheus, not stored here |

**No NFS state.** All writes are to local SSD (per `note-20260410-141200-storage-split-nas-local.md`: frequently-written state stays local; durable reference data on NAS — but our reference data is yfinance-sourced and regenerable, so no NFS dependency).

## Failure modes (Principle I — every spec includes this)

| Failure | Detection | Response | Recovery |
|---|---|---|---|
| yfinance rate-limit | HTTP 429 or empty response | exp backoff, 3 retries, then fail hard | next run picks up; `factor_ic_run_ok` flips to 0 → alert |
| OHLCV cache corruption | parquet read error | delete file, re-fetch | idempotent |
| Factor computation NaN explosion | > 20% NaNs in output | refuse to score; emit diagnostic | manual investigation via logs |
| Walk-forward insufficient windows | N_windows < 10 | emit `measurement-only` status flag | ship less strong claim |
| Observability scrape failure | Prometheus reports scrape_down | platform alert (not our responsibility) | platform alerts route through tenant manifest |
| Process crash mid-run | systemd/launchd restart | re-launch; last-good emit still valid | auto-recovery; state.json lets us skip completed phases |

## Observability contract (Principle II)

Exposed on `:9095/metrics` (Prometheus text format):

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `signal_platform_heartbeat_timestamp` | gauge | — | Unix ts, updated every 30s by scheduler |
| `signal_platform_last_run_status` | gauge | `run_type` ∈ {ic, score, walkforward, emit} | 1 if last run succeeded, 0 otherwise |
| `signal_platform_last_run_duration_seconds` | gauge | `run_type` | duration of last run |
| `signal_platform_factor_ic` | histogram | `factor`, `horizon` | IC distribution across symbols |
| `signal_platform_universe_size` | gauge | — | N symbols in last universe fetch |
| `signal_platform_yfinance_errors_total` | counter | `error_type` | retry exhaustion events |

**E2E probe:** `signal_platform_e2e_probe_ok` = 1 if the full pipeline (fetch → IC → score → walk-forward → emit) completed successfully within the last 7 days.

## Sequencing (Principle V — incremental verification)

Build in this order so each step is testable in isolation:

1. Universe fetcher + OHLCV cache → test: "fetch SPY, QQQ, MSFT, observe parquet files written + re-fetch is cache-hit"
2. Factor library (copied from financial-engine) → test: smoke test that factors compute on cached data
3. Cross-sectional IC engine → test: IC on 10-symbol toy universe, compare to manual calc
4. Composite scorer (residualized + equal-weight baseline) → test: equal-weight output matches sum/N; residualized output has zero mean for shared-variance inputs
5. Walk-forward validator → test: on synthetic data with known Sharpe, validator returns ≈ that Sharpe
6. Service wrapper + scheduler + metrics → test: `/metrics` responds, scheduler fires once
7. Watchlist emit → test: output file format valid JSON, status flag correct given input
8. Observability tenant registration → test: `platform.yaml` validates against observability schema; `obs register` succeeds

## Trade-offs

- **yfinance only vs OpenBB**: faster setup, no survivorship-bias improvement. Accepted.
- **Long-running service vs batch job**: matches observability platform contract. Cost: one always-on process. Acceptable.
- **Copied factors.py vs refactored dependency**: accepts code duplication in P1 to avoid blocking on financial-engine refactor. Cleanup in a later phase.
- **Grinold residualization vs simpler composites**: more complex to implement, but it's the load-bearing reason this project exists. Non-negotiable.
- **5y history vs 10y**: 5y keeps yfinance fetch fast and includes 2021 regime + 2022 bear + 2023-2025 recovery. Acceptable for P1; expand later if IC stability argues for it.

## Dependencies

- `pandas>=2.2`, `numpy>=1.26`, `scipy>=1.13` — core numerics
- `yfinance>=0.2.50` — data source
- `pyyaml>=6.0` — platform.yaml loading
- `prometheus-client>=0.20` — metrics
- `structlog>=24.1` — structured logging
- `click>=8.1` — CLI

No external services required beyond yfinance (public, free). Observability platform integration is additive (fails soft if platform is down).

## Open questions (deferred)

- Do we need daily runs instead of weekly for IC monitoring (vs watchlist generation)? Deferred — weekly for v1.
- Should the composite scorer ensemble across horizons (combine 1d+5d+20d) or pick one? P1 ships single-horizon outputs; ensembling is P2+.
- Threshold for `regime-alert` status — how do we detect IC sign flip? P1 flags if any factor's mean IC crosses zero in last 4 weeks; refine later.
