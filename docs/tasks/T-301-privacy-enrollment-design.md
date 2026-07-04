# T-301: Privacy enforcement + enrollment data model

**Agent:** claude · **Milestone:** M3 · **Depends on:** T-002
**Why this agent:** the highest-stakes design in the project — biometric data handling. Mistakes here are ethical failures, not just bugs.

## Context

ADR-0005 sets the invariants; handoff §2.3/§10 the requirements. This task produces the *design and enforcement mechanisms* T-302/T-303/T-304 implement against. Design-first because retrofitting privacy onto a working pipeline never happens.

## Deliverables

- Enrollment data model (schema + contracts additions if needed via ADR): person record (opaque `person_id`, display name, consent state + consent timestamp, revocation), embedding storage format (SSD-local SQLite per ADR-0007, file layout, deletion semantics that actually delete), profile assignment (profile_id, color, optional sound).
- Privacy enforcement points, specified concretely: where unknown-embedding discard happens in the pipeline (memory lifecycle — embeddings of unknowns must never touch disk or logs), what privacy-mode disables at which layer, how deletion propagates (embeddings, cached identities, ESP32 profile cache, events already emitted).
- Threat-model note: stolen-Pi scenario walkthrough — what biometric data exists on the SSD, mitigation options (at-rest encryption feasibility on Pi 5, trade-offs), recommendation.
- Test specification: the exact test cases T-302/T-303 must ship to prove non-persistence, deletion, privacy-mode, and log-cleanliness (grep-able assertions on log output).
- Review checklist addition to the PR template if needed (biometric-touching PRs).

## Out of scope

Implementation (T-302/T-303/T-304). Model/accelerator specifics beyond what data-shape requires.

## Acceptance criteria

- Design doc merged as `docs/adr/0009-enrollment-and-biometric-data.md` (or docs/ page + short ADR).
- Every ADR-0005 invariant maps to at least one named enforcement mechanism and one specified test.
- T-302/T-303/T-304 briefs updated (by this task) with concrete references into the design.
