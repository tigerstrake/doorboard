# Doorboard

[![CI](https://github.com/tigerstrake/doorboard/actions/workflows/ci.yml/badge.svg)](https://github.com/tigerstrake/doorboard/actions/workflows/ci.yml)

An open-source, self-hosted, door-mounted smart-door and smart-room system. A removable "door backpack" carries a large ambient display, a small visitor touchscreen, two cameras, a physical bell button with LED/audio feedback, and a Raspberry Pi 5 + Hailo AI accelerator — backed by an indoor NUC control plane, a BirdNET Pi, and a NAS archive.

**Status: architecture phase.** This repository currently contains the complete architecture, contracts, and task briefs — no implementation code yet. Implementation happens task-by-task via the multi-agent workflow described below.

## The one rule

> Any interaction at the physical door must react immediately and must never depend on the NUC, NAS, cloud services, Home Assistant, background jobs, or a fresh AI inference request.

Everything in this repo is structured around that rule. Read [ARCHITECTURE.md](ARCHITECTURE.md) before touching anything.

## Repository map

| Path | What it is |
|---|---|
| `ARCHITECTURE.md` | System design: planes, trust boundaries, latency budgets, service topology |
| `docs/handoff/` | The original architecture handoff document (source of truth for requirements) |
| `docs/adr/` | Architecture Decision Records — binding decisions |
| `docs/protocols/` | Typed event catalog, ESP32↔Pi wire protocol, API conventions |
| `docs/tasks/` | Self-contained task briefs, one per subtask, assigned to an agent tier |
| `docs/hardware/` | Bill of materials, door assembly, wiring |
| `docs/ui/` | Specs for the four UI surfaces (wallboard, doorpad, visitor, admin) |
| `docs/runbooks/` | Operational runbooks (written during M7) |
| `apps/` | Service specs (and later, code) for each deployable service |
| `firmware/` | ESP32-S3 door controller spec/firmware |
| `packages/` | Shared libraries: contracts, config, observability, auth, clients, ui-kit |
| `integrations/` | Adapters: BirdNET, satellites, aircraft, printer, Home Assistant, food, gallery |
| `deploy/` | Per-host deployment configs: door Pi, NUC, bird Pi, ADS-B Pi, NAS |
| `infra/` | Compose files, systemd units, reverse proxy, monitoring |
| `tests/` | Integration, e2e, hardware-in-loop, performance suites |
| `tools/` | Enrollment CLI, seed data, diagnostics |

## Multi-agent development workflow

Work is split into task briefs in [docs/tasks/](docs/tasks/), each assigned to an agent tier:

- **`agent:claude`** — architecture, contracts, security/privacy-critical decisions, and **review of every PR** (final say on all decisions).
- **`agent:codex`** — latency-critical and correctness-critical implementation: firmware, vision, media, sync, state machines.
- **`agent:gemini`** — bulk/tedious work: UI boilerplate, integration adapters, fixtures, config, docs, runbooks.

Rules of engagement are in [CONTRIBUTING.md](CONTRIBUTING.md). Agent-specific entry points: [CLAUDE.md](CLAUDE.md), [AGENTS.md](AGENTS.md) (Codex), [GEMINI.md](GEMINI.md).

## Development setup

Required tools: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 20+, and pnpm via Corepack.

From a fresh clone, install from the committed lockfiles:

```sh
uv sync
pnpm install
```

Run the quality gates directly or through the documented `Makefile` aliases:

```sh
scripts/lint && scripts/typecheck && scripts/test
make lint typecheck test
```

`scripts/dev-up` and `make dev-up` are placeholders until the simulated stack lands in T-003.

## Milestones

| Milestone | Goal |
|---|---|
| M0 | Repository bootstrap: tooling, CI, contracts package, simulator, mock UIs |
| M1 | Real-time I/O: ESP32 firmware, Pi↔ESP32 transport, latency harness |
| M2 | Camera + media: MediaMTX, WebRTC live view, SSD recording |
| M3 | Face recognition: Hailo pipeline, enrollment, identity cache, privacy behavior |
| M4 | Visitor session: state machine, DoorPad flow, video messages, guestbook/polls |
| M5 | Control plane: NUC API, Postgres, Home Assistant, statuses, NAS sync |
| M6 | Ambient features: birds, satellites, aircraft, printer, mood, food, photo booth |
| M7 | Hardening: security, monitoring, backups, runbooks, performance acceptance |

The success criterion for everything before M6:

> A person can approach the door, press a physical button, see and hear immediate feedback, interact with two displays, leave a video message, and trigger a personalized opt-in greeting — even if the NUC, NAS, or internet is temporarily unavailable.

## License

[MIT](LICENSE)
