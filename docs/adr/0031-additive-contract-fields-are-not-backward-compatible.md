# ADR-0031: additive contract fields are not backward compatible

**Status:** Accepted · **Date:** 2026-08-18

Corrects a claim made in [ADR-0025](0025-satellite-pass-geometry.md),
[ADR-0026](0026-dining-recommendation-names-its-hall.md),
[ADR-0029](0029-identity-expired-says-why.md) and
[ADR-0030](0030-a-satellite-pass-carries-its-ground-track.md). No code change.

## Context

Each of those four ADRs describes its change as "additive; an older producer still validates",
and each is correct about that direction. All four are silent about the other one, and the
other one is where the breakage lives.

`packages/contracts` models are `StrictModel`, i.e. `ConfigDict(extra="forbid")`. So a service
running older contracts **rejects** an event carrying a field it has never heard of. Verified:

```
parse_event({... "payload": {..., "some_future_field": "x"}})
  -> ValidationError: `ambient.food_recommendation`.payload.some_future_field
```

`control-plane-api` validates every ingested event with `parse_event`. So adding an optional
field is backward compatible for *readers of old data* and a hard break for an *old consumer
of new data*. Adding `hall` to the food payload does not degrade gracefully on a control plane
that predates it — it 422s the whole batch.

This surfaced concretely: the NUC was eleven days behind the door, and rebuilding only
`wallboard-worker` would have had the new worker publishing `track[].lat` into a control plane
that would refuse it. The stack had to move together.

## Decision

**A new field on an existing event requires every consumer to be deployed at or after the
producer.** In practice, for this system: the NUC (`control-plane-api` + `wallboard-worker`)
updates as a unit, and the door may lead it but not lag it, because door-visiond and door-api
publish upward through `/ingest`.

**An ADR adding a field must state the deployment ordering it implies**, not just that the
field is optional. "Additive" describes the schema; it does not describe the rollout.

**`extra="forbid"` stays.** It is what makes an unknown field a loud failure instead of a
silently ignored one, and that has caught real mistakes. The cost is this ordering
constraint, which is acceptable for a single-home system where both hosts are ours. It would
not be acceptable if third parties consumed these events, and that is the condition under
which this decision should be revisited.

## Consequences

- The four ADRs above are accurate about schemas and incomplete about rollout; this ADR is the
  correction rather than four edits.
- `deploy/nuc/README.md`'s update procedure should say the two NUC services move together.
  Splitting them to reduce risk is the intuitive move and the wrong one.
- A staged rollout across hosts is not available for contract additions. The mitigation is
  that the door keeps working without the NUC by design, so the NUC can be updated abruptly.
