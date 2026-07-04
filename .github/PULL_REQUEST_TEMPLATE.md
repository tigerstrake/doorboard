## Task

Closes #<!-- issue number -->
Brief: <!-- link to docs/tasks/T-XXX-*.md -->
Agent tier: <!-- claude / codex / gemini -->

## What changed

<!-- 2-5 sentences. What a reviewer needs to know that the diff doesn't say. -->

## Checklist (all required)

- [ ] Every acceptance criterion in the brief passes (list evidence below if not obvious from CI)
- [ ] Nothing added to the door critical path (no NUC/NAS/cloud waits, no cold starts on bell)
- [ ] Works in mock mode — no hardware needed for tests; CI green
- [ ] All messages use `packages/contracts` types (no ad hoc JSON)
- [ ] No secrets, tokens, or credentials in the diff; `.env.example` updated for new config
- [ ] Stayed inside the brief's scope (out-of-scope needs → escalation issue, linked here)

## Privacy checklist (required if the PR touches enrollment, embeddings, media, retention, logging, or any public UI)

- [ ] No unknown-face embeddings persisted or logged
- [ ] No biometric data, raw frames, or tokens in logs
- [ ] Public routes (`/wallboard`, `/doorpad`, `/visitor`) show broad/sanitized data only
- [ ] Deletion flows still work for affected data
- [ ] N/A — PR touches none of these surfaces

## Review

Awaiting Claude-tier review — do not merge before a review comment exists (ADR-0008).
