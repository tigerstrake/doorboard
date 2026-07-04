# T-304: Enrollment CLI + admin UI forms

**Agent:** gemini · **Milestone:** M3 · **Depends on:** T-302
**Why this agent:** forms/CLI over an existing, well-specified API. The sensitive logic already exists in door-visiond; this task must not reimplement any of it.

## Context

Specs: [tools/enrollment-cli/README.md](../../tools/enrollment-cli/README.md), [docs/ui/admin.md](../ui/admin.md) enrollment section, handoff §10 enrollment flow. Everything calls door-visiond's `/enroll`, `/unenroll`, and admin endpoints — no direct DB or file access from this code, ever.

## Deliverables

- `tools/enrollment-cli`: guided flow — consent confirmation (explicit y/N with recorded consent statement), capture N images with lighting/angle prompts (via door-media snapshot endpoint or documented capture path), submit to `/enroll`, assign profile (name, color from the T-103 effects catalog, optional sound), test-match step, `unenroll` command.
- Admin UI enrollment section: list enrolled people (name, profile, consent date), enroll flow mirroring the CLI (using existing capture/preview components), unenroll with confirm (states clearly that deletion is immediate and irreversible), privacy-mode toggle.
- Both surfaces show consent language from a single shared source file (docs-reviewed text, not improvised).
- Tests: UI component tests; CLI integration test against door-visiond mock mode.

## Out of scope

Anything inside door-visiond (bugs/missing endpoints → escalation), embedding/file handling, consent-language authorship (flag for Claude review if the placeholder text seems off), public UI.

## Acceptance criteria

- Full enroll→test-match→unenroll cycle works against mock-mode door-visiond in CI and against hardware on bench.
- No file in this PR touches embedding storage paths (reviewer will grep).
- Enrollment UI unreachable without admin auth; nothing enrollment-related is linked or fetchable from public routes.
