# T-001: CI pipeline + repo hygiene

**Agent:** gemini · **Milestone:** M0 · **Depends on:** T-000
**Why this agent:** well-specified, template-following configuration work.

## Context

T-000 made CI run lint/typecheck/test. This task rounds out CI quality-of-life and repo hygiene. Read [CONTRIBUTING.md](../../CONTRIBUTING.md) first.

## Deliverables

- `scripts/check-structure` (Python, stdlib only): verifies every brief in `docs/tasks/` has the required sections (Agent/Milestone/Depends-on header, Context, Deliverables, Out of scope, Acceptance criteria) and that all relative markdown links in `docs/` and root docs resolve. Wire it as a CI job.
- CI polish: path-filtered jobs (Python jobs skip on docs-only changes and vice versa), dependency caching for uv and pnpm, concurrency cancellation for superseded runs.
- `.editorconfig` consistent with Ruff/Prettier settings.
- PR title check: must start with `T-\d{3}:` or `docs:`/`fix:`/`chore:` (simple regex job, not a heavyweight action).
- README badges (CI status) and a `docs/` link-check exclusion list for external URLs (don't fail CI on flaky external sites — check internal links only).

## Out of scope

Changing lint/type rules (T-000 owns them), release/deploy automation, branch-protection settings (unavailable on this plan), anything under `apps/` or `packages/`.

## Acceptance criteria

- `scripts/check-structure` catches: a brief missing a section, a broken internal link (prove with a test fixture or unit test).
- CI on a docs-only change runs only the docs job; full pipeline < 5 minutes with warm caches.
- All jobs green on the PR itself.
