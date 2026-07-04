# T-703: Runbooks, monitoring dashboards, backup verification

**Agent:** gemini · **Milestone:** M7 · **Depends on:** T-503

## Context

Handoff §19.13 requires boot/repair/replacement/storage/network/NUC-outage runbooks; [docs/runbooks/README.md](../runbooks/README.md) lists the set. Monitoring per [infra/monitoring/README.md](../../infra/monitoring/README.md).

## Deliverables

- All runbooks from the required set **except** `security-checklist.md` (T-701 owns it): symptoms → diagnosis → fix steps → verification, written against the real deployed system, each walked through once for real (note the walkthrough date in the doc).
- Monitoring stack in `infra/monitoring/`: Prometheus scrape of every `/metrics` (Pi + NUC), Grafana dashboards for: §4 latency budgets, storage/queues, thermals/CPU, ESP32 link, service health; alert rules: storage low, sync queue aging > threshold, ESP32 offline > 1 min, Pi throttling, service down.
- Backup verification: scheduled restore-test procedure for Postgres dumps and config bundles, documented + demonstrated once.
- Alert routing to the owner (same notifier channel as T-501).

## Out of scope

New metrics (escalate to T-104 conventions), security checklist (T-701), performance suite (T-702).

## Acceptance criteria

- Each runbook executed once by following it literally (no unstated steps) — walkthrough notes included in the PR.
- Dashboards render real data from the running system; each alert rule test-fired once.
- Restore test: dump → restore to scratch DB → integrity check passes, procedure timed and documented.
