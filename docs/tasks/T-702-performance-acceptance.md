# T-702: Performance acceptance suite — the 30-minute soak

**Agent:** codex · **Milestone:** M7 · **Depends on:** all M0–M4 (runs on real hardware)
**Why this agent:** composing the harness into the mandatory acceptance test that gates ambient features.

## Context

Handoff §17 mandates this before ambient features run on the appliance: 30 minutes with both cameras, Hailo, two kiosk screens, WebRTC clients, repeated button presses, local recording, face events, and a forced NAS outage/reconnect — no thermal throttling, no growing backlog, no missed button events, stable UI, p95 targets met. Composes T-104's harness.

## Deliverables

- Orchestrated soak in `tests/performance/`: scripted load (press cadence, scripted enrolled/unknown arrivals, WebRTC client churn, recording cycles), mid-run NAS outage + recovery injection, continuous capture of: every §4 latency percentile, CPU/temp/throttle flags, camera frame drops, queue depths, memory per service, UI reconnect counts.
- Pass/fail evaluation against explicit thresholds (the §4 table + zero missed presses + zero backlog growth post-recovery + no throttling), machine-readable verdict + human report with graphs.
- Repeatability: one command on the bench rig, documented setup, results archived per run (`tests/performance/results/` gitignored, summary committed).
- Shorter CI-scale variant (simulator-based, minutes) keeping the orchestration code exercised.

## Out of scope

Fixing regressions it finds (issues filed per finding), dashboards (T-703), new instrumentation (escalate to extend T-104 conventions if a metric is missing).

## Acceptance criteria

- Suite runs end-to-end on the real door rig and produces a verdict; a deliberately induced fault (e.g. throttled CPU via stress) correctly fails it.
- The real rig **passes** — or every failure has a filed, severity-labeled issue and the milestone review (T-790) decides.
- Verdict + report attached to the PR; rerun variance documented (< agreed tolerance between consecutive passing runs).
