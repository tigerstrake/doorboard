# Contributing — the multi-agent workflow

This project is built by a coordinated set of AI agents with a human owner. The workflow is a binding decision (ADR-0008). Humans are welcome to follow the same process.

## Roles

| Tier | Label | Responsibility |
|---|---|---|
| **Claude** (coordinator) | `agent:claude` | Architecture, event contracts, security- and privacy-critical work, ADRs, and **review of every PR**. Claude has final say on all decisions. |
| **Codex** (senior implementer) | `agent:codex` | Correctness- and latency-critical implementation: ESP32 firmware, door-visiond, door-media, door-sync, session state machine, performance harness. |
| **Gemini** (bulk implementer) | `agent:gemini` | High-volume, well-specified work: UI boilerplate, integration adapters, CRUD features, fixtures, config authoring, docs, runbooks. |

## The task lifecycle

1. **Pick a task.** Every unit of work is a brief in [docs/tasks/](docs/tasks/) with a matching GitHub issue carrying an `agent:*` label and an `M0`–`M7` milestone. Do not start work that has no brief.
2. **Read before writing.** Required reading for every task: your agent entry file (`CLAUDE.md` / `AGENTS.md` / `GEMINI.md`), [ARCHITECTURE.md](ARCHITECTURE.md), the brief itself, and every doc the brief links. Briefs are self-contained on purpose — if yours isn't, that's a bug in the brief; flag it instead of guessing.
3. **Branch.** `task/T-<id>-<slug>` off `main`, e.g. `task/T-101-esp32-firmware-core`. One task = one branch = one PR.
4. **Respect dependencies.** Each brief lists `Depends on:`. Do not start a task whose dependencies aren't merged.
5. **Implement inside the fence.** The brief's *Out of scope* section is binding. If the task seems to require changing something out of scope — especially `packages/contracts` or anything in ARCHITECTURE.md — stop and escalate (see below).
6. **Open a PR** using the template. Link the issue (`Closes #N`), fill in the privacy checklist, ensure CI is green.
7. **Claude reviews.** No PR merges without review by the Claude tier. Codex/Gemini never merge their own work. Note: branch protection cannot be enforced on this private free-plan repo, so this rule is procedural — the human owner merges only after a Claude review comment exists.

## Escalation

Stop and open an issue labeled `escalation` (assigned to `agent:claude`) instead of improvising when:

- the brief conflicts with ARCHITECTURE.md or the handoff document;
- you need a contracts change (new event type, changed field) — contracts changes require an ADR;
- you need a new dependency not listed in the brief or ADR-0003;
- the task touches privacy-sensitive surfaces (enrollment, embeddings, retention, public display content) in a way the brief didn't anticipate.

## Hard rules (from the handoff — apply to every PR)

- Never make button feedback depend on NUC HTTP or NAS write success.
- Never persist unknown-face embeddings; never log raw biometric data.
- Never put secrets in git — `.env.example` only.
- Never use microSD paths for recordings or active databases.
- Never treat face matching as authorization.
- Never expose private data on public routes (`/wallboard`, `/doorpad`, `/visitor`).
- Hardware must be optional: every service runs in mock/simulation mode in dev and CI.
- All inter-service messages use `packages/contracts` types — no ad hoc JSON.
- UTC internally; local timezone only at the display boundary; monotonic time for latency/expiry.

## Code standards

- **Python:** 3.12+, FastAPI, Pydantic v2. Formatted/linted by Ruff, typed to pass Pyright. Tests with pytest; new logic needs tests.
- **TypeScript:** strict mode, React + Vite. Shared visual components go in `packages/ui-kit`.
- **Commits:** imperative subject, reference the task ID, e.g. `T-101: debounce button ISR`.
- **Logs:** structured JSON with `service`, `event_id`, `trace_id`, `session_id`, timestamps, latency, error fields.
- Every service ships `GET /health` and `GET /metrics`.

## Definition of done (all tasks)

- Acceptance criteria in the brief all pass.
- CI green (lint, typecheck, tests).
- Works with hardware absent (mock mode) — CI has no cameras, Hailo, or ESP32.
- No new warnings, no leftover debug output, no TODOs without a linked issue.
- Docs updated if behavior or interfaces changed.
