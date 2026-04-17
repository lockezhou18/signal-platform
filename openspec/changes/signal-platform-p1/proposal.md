# Proposal: signal-platform-p1 — Cross-sectional IC engine + ranking baseline

**Stage:** PLAN (Stage 2 of /lfg)
**Scope:** Standard (first of 7 phases in a Heavy roadmap)
**Status:** draft — awaiting user review to gate BUILD

## What

Build the **cross-sectional signal foundation** for the two-track signal platform:

1. **Universe fetcher** — S&P 500 + QQQ-100 constituents, daily OHLCV via yfinance, disk-cached, survivorship-bias-documented.
2. **Factor IC engine (cross-sectional)** — extend `factor_ic.py` pattern from `financial-engine` to rank-correlate factors across a universe at each timestamp; walk-forward stability over 5y daily history.
3. **Composite scorer with Grinold residualized weighting** — step 9 of the 11-step alpha combination procedure (`team-shared/knowledge/sources/alpha-combination-20260416/rohonchain-math-behind-50-weak-signals.md`). Explicitly compared to equal-weight baseline.
4. **Walk-forward validator** — top-decile weekly rebalance simulator; reports OOS Sharpe across ≥10 non-overlapping windows.
5. **Weekly watchlist emit** — top-N names (configurable; default N=20) written to a shared location with status flag (`validated` / `measurement-only` / `regime-alert`).
6. **Observability tenant integration** — `platform.yaml` manifest registered with `lockezhou18/observability`; `/metrics` endpoint; heartbeat + e2e probe; default Grafana dashboard.

## Why

Our signal pipeline today measures **time-series IC on 4 symbols** (`financial-engine/trade/backtest/factor_ic.py`). This answers "does factor X predict THIS stock's return?" — useful for wheel-delta tuning, useless for screening.

The actual portfolio decision is cross-sectional: among the universe today, which names rank highest on composite signal? Phase 1 builds the minimum viable engine to answer that question honestly — IC-measured, walk-forward-validated, observability-instrumented — before we add any new signal family.

Phase 1 also **establishes the engineering contract** (tenant-compatible, observability-integrated, CI-gated) that later phases (P2 fundamentals, P3 insider, etc.) plug into without further platform work. The engineering principles note (`team-shared/engineering-principles.md`) is non-negotiable here: failure modes in every spec, observability requirements in every spec.

## Success criteria

- Cross-sectional IC table for all ~45 existing price-volume factors × S&P 500 × {1d, 5d, 20d} horizons
- Walk-forward stability report with ≥10 non-overlapping windows over 5y
- Composite scorer implements Grinold step 9 (residualization); explicit A/B vs equal-weight
- Top-20 weekly watchlist emitted to `~/signal-platform-output/` or equivalent, status-flagged
- `/metrics` endpoint exposes `signal_platform_heartbeat_timestamp`, `signal_platform_last_run_status`, `signal_platform_factor_ic_histogram`
- Registered as tenant in observability platform (manifest + `obs register` run)
- CI green: ruff + ruff-format + mypy + pytest, all clean
- **Decision gate on validation bar:** if walk-forward top-decile weekly rebalance Sharpe ≥ 0.8 on ≥10 windows → watchlist flagged `validated`; if 0.5–0.8 → `measurement-only`; < 0.5 → block emit, write failure analysis

## Out of scope (explicitly deferred to later phases)

- Fundamentals signals (P2 — OpenBB `obb.equity.fundamental.*`)
- Insider buying (P3 — SEC Form 4 via OpenBB)
- Earnings surprises + revisions (P4)
- Narrative sentiment scoring (P5 — intel platform + LLM)
- Options flow / unusual volume (P6)
- Macro regime filter (P7)
- Order execution — signal-platform is **read-only**; execution stays in `financial-engine`
- Real-time data — P1 uses daily bars end-of-day via yfinance

## Decision commitments

- **Universe:** S&P 500 (initial); QQQ-100 union added in the same PR if bandwidth allows
- **Data source:** yfinance only (cached to `~/signal-platform-data/ohlcv/<symbol>.parquet`)
- **Horizons:** 1d, 5d, 20d forward simple returns
- **IC metric:** Spearman rank correlation, point-estimate + standard error
- **Weighting:** Grinold residualized (step 9) as primary; equal-weight as comparison baseline
- **Cadence:** Service runs continuously; scheduler triggers weekly Sunday 6pm PT; manual trigger via CLI
- **Tenancy:** Long-running service (not batch) to expose `/metrics` continuously, matching observability platform v1 contract
