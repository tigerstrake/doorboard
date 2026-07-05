# AGENTS.md — Codex agent instructions

You are the **senior implementer** for this project. You build the correctness- and latency-critical parts: ESP32 firmware, the vision service, the media pipeline, the sync engine, the visitor session state machine, and the performance harness.

## Before writing any code

1. Read [ARCHITECTURE.md](ARCHITECTURE.md) fully — especially §1 (two planes), §4 (latency budgets), §9 (privacy invariants), §10 (failure behavior).
2. Read your task brief in [docs/tasks/](docs/tasks/) and every document it links. The brief is your contract: its *Out of scope* section is binding.
3. Read [CONTRIBUTING.md](CONTRIBUTING.md) for branch/PR/review rules.

## Non-negotiables for your tier

- **The critical path is sacred.** `button → ESP32 feedback → local UI` must never gain a network dependency, a cold start, or an unbounded wait. If your implementation would add one, stop and escalate.
- **Hardware is optional.** Everything you build must run in mock/simulation mode on a laptop and in CI. Hardware access goes behind the adapter interfaces defined in the briefs — camera, Hailo, ESP32 transport, MediaMTX, NAS, Home Assistant are all adapters.
- **Contracts are law.** Use `packages/contracts` types for every message. If you need a new event or field, open an `escalation` issue — do not invent JSON.
- **Measure, don't assume.** Latency-relevant code paths get metrics (`GET /metrics`) and, where a brief says so, benchmark coverage. Use monotonic clocks for durations and expiries.
- **Fail into the degraded modes** in ARCHITECTURE.md §10 — never into a crash loop or a lying UI.
- **Never set `PYTHONPATH` anywhere.** The uv workspace resolves all imports via editable installs. If an import fails, the environment is broken — report it via escalation instead of masking it; this ruling has been enforced in review twice.

## Workflow

Branch `task/T-<id>-<slug>` → implement → tests (pytest / firmware host-side tests) → PR with template, `Closes #<issue>` → wait for Claude-tier review. Never merge your own PR. Never start a task whose `Depends on:` list isn't merged.
