# Build guide: signal-platform-p1

Engineering opinions for this specific build. Maps `team-shared/engineering-principles.md` + CLAUDE.md to concrete choices for this project.

## Python style

- **Target Python 3.11** in `pyproject.toml` despite dev env being 3.14. Reason: CI runner uses setup-python@v5 with `3.11`; staying 3.11-compatible avoids f-string / typing surprises (see `note-20260414-223000-fresh-venv-catches-hidden-deps.md`).
- **Type-hint everything.** Mypy strict mode is on. If a symbol can't be typed, add `# type: ignore[<specific>]` with a reason.
- **Dataclasses > dicts for structured returns.** The `WalkForwardResult` example in the spec is the pattern.
- **No `print()`.** structlog everywhere. Log fields are lowercase snake_case.
- **No bare exceptions.** Catch specific exceptions; re-raise with context.
- **F-strings for interpolation.** `%`-formatting banned except in logging calls (`logger.info("msg", key=value)`).

## Module layout

```
src/signal_platform/
  __init__.py           # version, public API
  cli.py                # click-based entrypoints
  service.py            # long-running process wrapper
  scheduler.py          # APScheduler / stdlib Timer
  metrics.py            # prometheus_client registry + HTTP server
  logging.py            # structlog config
  data/
    universe.py         # S&P 500 / QQQ-100 tickers + scrape fallback
    ohlcv.py            # yfinance fetch + parquet cache
    sp500_snapshot.txt  # hardcoded fallback list
  factors/
    __init__.py         # compute_all_factors + individual factor fns (copied from financial-engine)
  signals/
    ic_engine.py        # cross-sectional IC computation
    scorer.py           # equal-weight + Grinold residualized
    walk_forward.py     # validator
  emit/
    watchlist.py        # JSON + markdown output
```

**No deep subpackages.** Two levels max (`signal_platform.signals.scorer`), never three.

## Testing

- **Test fixtures live in `tests/fixtures/`.** Cached parquet OHLCV for SPY/QQQ/MSFT committed (small files, 5y daily = ~50KB per symbol) so tests are hermetic.
- **No network in tests.** Ever. If a function needs network, mock it.
- **Parametrize over horizons.** `@pytest.mark.parametrize("horizon", [1, 5, 20])`.
- **Coverage target: 80%.** Not 100%; don't chase coverage by writing pointless tests.
- **Property-based tests** welcome (hypothesis library) for the residualization math.

## Observability engineering

- **Every entry point logs the run ID.** Generate a UUIDv7 on service start; attach to every structlog event via `structlog.contextvars.bind_contextvars`.
- **Metric names follow Prometheus naming conventions.** `signal_platform_` prefix for every metric. Units in the metric name (`_seconds`, `_total`, `_timestamp`).
- **No high-cardinality labels.** `factor` (~45) and `horizon` (3) are fine. `symbol` (~500) is NOT a label — we'd blow up the metrics cardinality. Per-symbol stats go to logs or parquet files, not Prometheus.
- **Heartbeat interval: 30s.** Balances liveness detection vs log noise.

## Data hygiene

- **All paths via `pathlib.Path`.** No string concatenation.
- **All timestamps timezone-aware (UTC).** Never naive. Yfinance returns America/New_York; convert explicitly.
- **Parquet not CSV.** Smaller, faster, preserves dtypes. CSV only for human-readable emit artifacts.
- **No silent data fill.** NaN propagates until we explicitly decide (per spec).

## CI-specific rules

- **Gitea runner has limited RAM.** Don't fetch full universe in CI (use committed fixtures).
- **Tests must finish in < 90s** on CI. If they don't, the fixture is too big.
- **No secrets in CI.** yfinance is public; no API keys needed for P1.

## Commit hygiene

- **Conventional commit prefixes.** `feat(T10):`, `fix(T12):`, `docs(T22):`, `chore(CI):`, `refactor(scorer):`, `test(ic-engine):`.
- **One task per commit** where practical; T10 can be one commit.
- **PR descriptions link to this OpenSpec change** and the task IDs closed.
- **No squash merges.** We want per-task history for post-hoc IC debugging.

## Deployment decisions (to finalize in PR 3)

- **Docker vs launchd:** start with launchd (mac-mini native, simpler). Docker migration if we need isolation.
- **Restart policy:** always. Max 5 restarts in 60s → drop to once-per-minute retry.
- **Logs:** stdout, captured by launchd to `/var/log/signal-platform/`. Rotated via newsyslog.
- **Data directory:** `~/signal-platform-data/` (local SSD, per state-management spec).
- **Output directory:** `~/signal-platform-output/` (local SSD, but mirrored to NAS `/volume1/stronghold/signal-platform-output/` by a separate rsync cron — NOT this project's responsibility).

## Anti-patterns we will NOT adopt

- **Equal-weight composite as the "default"** — only as A/B baseline. The recommended scorer is Grinold-residualized.
- **"Backtest looks great on full period"** without walk-forward — every performance claim is walk-forward.
- **Auto-trading based on watchlist** — signal-platform is READ-ONLY. Execution stays in financial-engine.
- **Manual factor tuning post-hoc** — once a phase is merged, factor choices are locked. New factor families = new phase.
- **Metrics-as-logs or logs-as-metrics** — structured logs for events; Prometheus for aggregates. Don't mix.
