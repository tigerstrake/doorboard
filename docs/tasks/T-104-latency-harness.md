# T-104: Latency measurement harness

**Agent:** codex · **Milestone:** M1 · **Depends on:** T-101, T-102
**Why this agent:** measurement correctness is subtle (clock domains, percentiles) and every future performance claim rests on it.

## Context

ARCHITECTURE.md §4 budgets "must be measured with metrics, not assumed" (handoff §6.2, §19.8). Build the harness early so every later milestone reports against it. Lives in `tests/performance/` + `packages/observability` helpers.

## Deliverables

- Instrumentation conventions: named measurement points for each §4 path, emitted as `system.latency_sample` events + Prometheus histograms via `packages/observability` (pre-declared histogram helpers per path).
- Cross-device timing method for button→feedback: ESP32 timestamps on its monotonic clock; document and implement the offset-estimation approach (e.g. ack round-trip halving) with stated error bounds — never assume synchronized clocks.
- Software harness: scripted runs against the simulator producing p50/p95/p99 tables per path; JSON + human-readable report output; regression comparison against a committed baseline file.
- Hardware harness mode: same reports driven by real bench presses (works with T-101/T-102); procedure documented in `tests/hardware-in-loop/`.
- CI job running the simulator-based suite with generous thresholds (catch order-of-magnitude regressions, not noise).

## Out of scope

The 30-minute acceptance soak (T-702 composes this harness), optimizing anything (measure first), dashboards (T-703).

## Acceptance criteria

- One command produces the latency table for all currently-implemented paths, on simulator and on bench.
- Percentile math unit-tested; clock-offset method documented with measured error bound on bench.
- Baseline file + CI regression check in place; intentionally slowed code path (test fixture) trips the check.
