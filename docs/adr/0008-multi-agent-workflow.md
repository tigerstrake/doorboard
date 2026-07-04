# ADR-0008: Multi-agent development workflow

**Status:** Accepted · **Date:** 2026-07-04

## Context

The project owner wants to build this system using multiple AI coding agents with different cost/capability profiles: Gemini (cheap, high-volume), Codex (strong implementation), and Claude (highest capability, reserved for architecture and judgment). Coordination failures between agents — contract drift, scope creep, privacy regressions — are the main risk.

## Decision

1. **Three tiers with fixed responsibilities** (see CONTRIBUTING.md): `agent:gemini` for well-specified bulk work, `agent:codex` for correctness/latency-critical implementation, `agent:claude` for architecture, contracts, privacy/security-critical work, and review of **every** PR. Claude has final say on all decisions.
2. **Work only flows through task briefs.** Every task is a self-contained file in `docs/tasks/` mirrored by a GitHub issue with agent label and milestone. Briefs specify deliverables, out-of-scope boundaries, dependencies, and acceptance criteria. No brief, no work.
3. **One task = one branch (`task/T-<id>-<slug>`) = one PR**, merged only after a Claude-tier review. Implementing agents never merge their own PRs.
4. **Escalation instead of improvisation.** Conflicts with architecture, needed contract changes, new dependencies, or unexpected privacy-sensitive surface area stop the task and produce an `escalation` issue for the Claude tier.
5. **Assignment heuristic** for future tasks: does a mistake here corrupt a contract, leak private data, or break the latency budget? → Claude designs or reviews closely, Codex implements. Is it template-following volume work with clear acceptance criteria? → Gemini. Everything between → Codex.

## Consequences

- Briefs must be written to be executable without conversation context — Gemini briefs especially are literal and exhaustive.
- Review is the bottleneck by design; it is where quality is enforced.
- GitHub branch protection is unavailable on this private free-plan repo, so the no-self-merge rule is procedural until the repo goes public.
