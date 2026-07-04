# T-000: Monorepo tooling bootstrap

**Agent:** codex · **Milestone:** M0 · **Depends on:** —
**Why this agent:** tooling decisions here shape every later task; getting workspace/CI ergonomics right requires judgment.

## Context

The repo currently contains architecture docs only. Establish the build/test/lint substrate every other task stands on. Stack is fixed by [ADR-0003](../adr/0003-technology-stack.md) — do not substitute tools.

## Deliverables

- **Python workspace:** `uv` workspace rooted at repo top; member `pyproject.toml` stubs for `packages/{contracts,config,observability,auth,event-client}` and `apps/{door-api,door-visiond,door-media,door-sync,control-plane-api,wallboard-worker,simulator}` (empty `src/` packages with `__init__.py`, no logic).
- **TS workspace:** `pnpm-workspace.yaml`; package stubs for `apps/door-ui`, `packages/ui-kit`, `packages/media-client` (Vite/React for door-ui, strict tsconfig shared via a base config).
- **Quality gates:** Ruff (format+lint) and Pyright configured workspace-wide; pytest wired with one trivial smoke test per Python package; ESLint + `tsc --noEmit` for TS packages.
- **Scripts:** `scripts/lint`, `scripts/typecheck`, `scripts/test`, `scripts/dev-up` (placeholder that will start the simulated stack from T-003 on).
- **CI activation:** the existing `.github/workflows/ci.yml` runs these for real (it currently runs structure checks only) — update it so lint/typecheck/test execute and pass.
- Root `Makefile` or `justfile` (pick one, document it) mapping to the scripts.

## Out of scope

Any business logic, contracts models (T-002), simulator behavior (T-003), UI beyond empty Vite scaffold (T-004), Docker/compose (T-503).

## Acceptance criteria

- Fresh clone + documented two-command setup → `scripts/lint && scripts/typecheck && scripts/test` all green locally and in CI.
- `uv sync` and `pnpm install` both resolve from lockfiles (committed).
- No tool warnings about misconfiguration; Pyright runs in `strict` for `packages/`, `basic` acceptable for `apps/` stubs.
- README gains a short "Development setup" section.
