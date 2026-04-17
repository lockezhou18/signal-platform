# Runbook: signal-platform

## What is signal-platform?

Multi-signal quantitative stock screener that ranks a broad US equity universe by orthogonal factor signals (Grinold-residualized composite), validates via walk-forward backtest, and emits a weekly top-20 watchlist. Phase 1 of 7 in a staged signal-stacking roadmap.

Read-only — no order execution, no credentials required beyond public data (yfinance).

## How do I know it's healthy?

```bash
curl -s http://localhost:9095/healthz
# expect: {"status":"ok","service":"signal-platform","heartbeat_ts":<recent>,"uptime_s":<N>}
```

Heartbeat should be within 120 seconds of current time. Grafana `signal-platform` dashboard should show green on heartbeat + e2e probes.

## How do I restart?

```bash
launchctl kickstart -k gui/$UID/com.openclaw.signal-platform
# or fallback:
launchctl unload ~/Library/LaunchAgents/com.openclaw.signal-platform.plist
launchctl load   ~/Library/LaunchAgents/com.openclaw.signal-platform.plist
```

Verify:
```bash
curl -s http://localhost:9095/healthz
```

## Alert: stale_heartbeat

**Meaning:** `platform_heartbeat_timestamp` has not been updated in >120 seconds. The service is either crashed, hung, or networking-blocked.

**Investigate:**
1. `launchctl list | grep signal-platform` — is the process alive?
2. `tail -200 /var/log/signal-platform/service.log` — any fatal errors?
3. `curl http://localhost:9095/healthz` — does the port respond at all?

**Remediate:**
- If process is dead → `launchctl kickstart -k gui/$UID/com.openclaw.signal-platform`
- If port is blocked → check for port conflict (`lsof -iTCP:9095 -sTCP:LISTEN`)
- If repeated crash → check recent commits for regressions; revert if necessary

## Alert: e2e_probe_failing

**Meaning:** `platform_signal_platform_probe_ok` is 0. The last full pipeline run (fetch → IC → score → walk-forward → emit) did not complete within the last 7 days.

**Investigate:**
1. `grep pipeline_ /var/log/signal-platform/service.log | tail -50`
2. Check `signal_platform_yfinance_errors_total` — is yfinance rate-limiting us?
3. Check `signal_platform_ic_low_coverage_total` — did the universe shrink?

**Remediate:**
- yfinance rate-limit: wait for backoff, confirm next weekly tick recovers
- Universe issue: inspect S&P 500 fallback snapshot; re-scrape Wikipedia

## Alert: walkforward_regime

**Meaning:** `platform_signal_platform_walkforward_status` dropped below 1 (i.e. is at `regime-alert`). The top-decile weekly rebalance strategy is no longer producing Sharpe ≥ 0.5 across windows, OR factor IC signs flipped recently.

**Do NOT panic-act.** This is a watchlist annotation, not a live-trading signal. Downstream consumers (quant-advisor, Binghua) should reduce weight on this run's recommendations until the next weekly recomputes a stable status.

**Investigate:**
1. Inspect `~/signal-platform-output/` for the most recent walk-forward detail JSON
2. Check whether IC sign flip is isolated (one factor, rotated out) vs systemic (many factors, genuine regime shift)

## Alert: yfinance_errors_spike

**Meaning:** More yfinance errors than the threshold in the last 15 minutes. Likely rate-limit or yfinance API change.

**Investigate:** `grep yfinance /var/log/signal-platform/service.log | tail -30`

**Remediate:** If sustained, consider switching to cached-only runs until yfinance recovers; check `yfinance` GitHub issues for known outages.

## Who to page

Binghua.

## Escalation

None — single-user, personal infrastructure. If heartbeat is silent > 1 day, Binghua investigates manually.
