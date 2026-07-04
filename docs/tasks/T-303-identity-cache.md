# T-303: Identity cache + ESP32 personalization

**Agent:** codex · **Milestone:** M3 · **Depends on:** T-302, T-102
**Why this agent:** the cache is the mechanism that reconciles "instant response" with "recognition takes time" — the architectural crux.

## Context

ARCHITECTURE.md §5 (identity-cache behavior) is the normative sequence. This task wires door-visiond's stable identities to the ESP32 profile cache and the UI greeting path.

## Deliverables

- `current_visitor` cache in door-visiond: TTL 2.5 s (config), refreshed on continued stability, expired → `identity_expired` + `door.profile_clear`.
- Profile push: `identity_stable` → `door.profile_update` over Esp32Transport with TTL semantics (never absolute time — ADR-0006); priority handling; confirmation tracked (ack) with retry; ESP32-offline → cache still serves the UI path, admin warning surfaced.
- door-api integration: button press consults cache → personalized vs generic session greeting, `had_cached_profile` propagated into session events; **late recognition may update the display but never delays or re-triggers the initial interaction**.
- Greeting cooldown enforcement (30 s/person) across the visiond→esp32→ui path (single source of truth in visiond).
- Simulator scenario updates: `known-visitor` exercises the full cache path timing.

## Out of scope

Vision pipeline internals (T-302), UI visuals (T-404 handles visitor-mode rendering), firmware cache logic (exists from T-101).

## Acceptance criteria

- Bench, T-104-measured: button with warm cache → personalized effect p95 < 100 ms; cold cache → generic < 30 ms with **zero** added latency from recognition machinery.
- Cache-expiry drill: person leaves, TTL lapses → ESP32 confirmed cleared (heartbeat `cached_profile_id` null) within 1 s of expiry.
- Race tests: press exactly at expiry boundary, identity arriving mid-session, privacy-mode flip mid-cache — all deterministic, all covered by integration tests against the simulator.
