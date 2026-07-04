# T-x90: Milestone review + acceptance (template)

**Agent:** claude · **Milestone:** each (T-090, T-190, T-290, T-390, T-490, T-590, T-690, T-790) · **Depends on:** all tasks in the milestone
**Why this agent:** cross-cutting judgment; this is the quality gate ADR-0008 builds the workflow around.

## Context

Every milestone ends with a Claude-tier review pass over the milestone as a whole — beyond the per-PR reviews already done. The review has final say; a milestone is not "done" because its issues are closed.

## Standard checklist (all milestones)

- Re-verify the four invariant families on the milestone's merged surface: critical-path integrity, privacy invariants (ARCHITECTURE.md §9), trust boundaries, contract discipline.
- Run the full test suite + relevant simulator scenarios; confirm no latency-budget regressions in `tests/performance/` output.
- Check for drift: docs/specs still match implementation; briefs of *future* tasks still valid given what was learned — refine them and note changes in the issue.
- Write/append ADRs for any decision that emerged during implementation.
- Produce a short milestone report in the issue: what shipped, deviations, risks carried forward, go/no-go for next milestone.

## Milestone-specific additions

| Review | Extra checks |
|---|---|
| T-090 (M0) | Fresh-clone setup works as documented; scenario determinism; contracts vs events.md exact match |
| T-190 (M1) | **On hardware:** button→LED p95 < 30 ms, cached-profile p95 < 100 ms measured by T-104 harness; fallback drill (kill Pi, press button) |
| T-290 (M2) | **On hardware:** thermal/CPU soak with stream + recording; WebRTC latency < 750 ms; no cold-start on bell |
| T-390 (M3) | **Privacy audit:** unknown-embedding non-persistence proven; privacy-mode drill; enrollment deletion verified on disk; no biometric data in any log |
| T-490 (M4) | Full e2e visitor journey on hardware; session survives kiosk reload; UGC sanitization pen-check (script/HTML injection attempts) |
| T-590 (M5) | Outage drill: NUC down 30 min under use → zero door impact, queue drains clean on recovery; token scope check on Pi |
| T-690 (M6) | Each ambient tile degrades independently; feature flags verified; no integration credential on the Pi |
| T-790 (M7) | The handoff success criterion, end-to-end, with NUC+NAS+internet disconnected; security checklist (T-701) all-green; sign off the release |
