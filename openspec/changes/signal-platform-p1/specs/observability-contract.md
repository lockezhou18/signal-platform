# Spec: observability-contract

## Purpose

signal-platform joins the `lockezhou18/observability` platform as a first-class tenant per the tenant contract defined in `observability/specs/observability-platform-architecture.md`. This spec is the platform-facing face of signal-platform.

## Tenant manifest

`platform.yaml` at repo root. Modeled on `reference-tenants/hello-world/platform.yaml`.

```yaml
apiVersion: observability.platform/v1
kind: Tenant
metadata:
  name: signal-platform
  owner: binghua
  repo: https://github.com/lockezhou18/signal-platform
  runbook: docs/runbooks/signal-platform.md

deployment:
  kind: docker            # or: launchd — TBD before BUILD
  name: signal-platform
  host: mac-mini

scrape:
  endpoint: http://localhost:9095/metrics
  interval: 30s
  timeout: 10s

probes:
  - name: heartbeat
    type: heartbeat
    metric: platform_heartbeat_timestamp
    max_age_seconds: 120
    severity: warning

  - name: last_run_ok
    type: e2e
    metric: platform_signal_platform_probe_ok
    threshold: 1
    comparison: lt
    severity: critical

  - name: walkforward_regime
    type: e2e
    metric: platform_signal_platform_walkforward_status
    threshold: 1
    comparison: lt
    severity: warning

alerts:
  default_rules:
    - stale_heartbeat
    - e2e_probe_failing
    - scrape_down
  extra_rules:
    - yfinance_errors_spike
    - ic_low_coverage_run

dashboard:
  template: default_docker_service   # or: custom if built
  custom_panels:
    - factor_ic_heatmap
    - weekly_watchlist_table
    - walkforward_sharpe_history
  refresh: 30s
```

## Metrics endpoint

- HTTP server on `:9095` (configurable via `SIGNAL_PLATFORM_METRICS_PORT` env)
- Path `/metrics` returns Prometheus text format (via `prometheus_client.generate_latest`)
- Path `/healthz` returns JSON `{"status":"ok", "uptime_s": N, "last_run": timestamp}`
- Listens on `0.0.0.0` (gitea-runner + Prometheus scrape both reach localhost)

## Required metrics (contracted)

Per sub-spec requirements (universe-fetcher, factor-ic-engine, composite-scorer, walk-forward-validator). Aggregate:

- `platform_heartbeat_timestamp` — always-on scheduler updates every 30s
- `platform_signal_platform_probe_ok` — set to 1 if pipeline completed ≤ 7d ago, 0 otherwise
- `signal_platform_last_run_status{run_type}` — last run per subsystem
- `platform_signal_platform_walkforward_status` — 0/1/2 enum
- Plus the per-subsystem counters/histograms/gauges listed in each spec

## Failure modes

| Failure | Response |
|---|---|
| Metrics port conflict | Log ERROR, increment startup; readiness probe fails |
| prometheus_client raises on registration | Abort process; restart via launchd/docker |
| Observability platform unreachable | No action needed (Prometheus scrape pulls; we don't push) |
| platform.yaml invalid per schema | `obs register` fails; correct and retry |

## Runbook

`docs/runbooks/signal-platform.md` must contain:

1. **What is signal-platform?** One paragraph.
2. **How do I know it's healthy?** `curl localhost:9095/healthz` returns 200 with `last_run` within 7d.
3. **How do I restart?** Systemd/launchd command.
4. **What if heartbeat alert fires?** Check process running; check `/metrics` reachable; check logs.
5. **What if e2e probe alert fires?** Inspect last run logs; usually yfinance rate-limit or data corruption; manual re-run.
6. **What if walkforward_regime alert fires?** Status flipped to `regime-alert`; review factor IC stability; consider pausing watchlist consumer.
7. **Who to page.** Binghua.

## Registration flow

1. Merge signal-platform-p1 to main
2. Clone repo to deployment host (mac-mini)
3. `cd signal-platform && obs validate platform.yaml` — must pass
4. `obs register` — writes tenant manifest to observability platform registry at `/Volumes/stronghold/platform/`
5. Prometheus file_sd_configs picks up the new target automatically (per observability platform spec)
6. Verify Grafana fleet overview shows signal-platform within 1 minute

## Testing

- Unit: `platform.yaml` parses as valid YAML; schema validation passes
- Integration: start the service, scrape `:9095/metrics`, verify all contracted metrics present
- E2E: `obs register` against a test registry; confirm tenant appears in Prometheus targets
