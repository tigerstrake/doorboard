# ADR-0044: the public relay discloses attribution without carrying the name

**Status:** Accepted · **Date:** 2026-08-25 · **Amends:** ADR-0017 §2 (visitor snapshot allow-list) and ADR-0018 §2 / E-23 (attribution disclosure) · **Upholds:** ADR-0016 §9 (the relay is a hostile courier), ADR-0005 §5 (public surfaces show broad status only)

## Context

ADR-0018 §2 amended the ADR-0017 §2 visitor-snapshot allow-list to add `attributed_to` — the
recognised person's real display name — so the visitor page could disclose "posting as Tiger"
before a write (E-23). The intent was "that person's own name shown back to them."

The intent does not hold on the relay path. The relay serves the snapshot to **anyone who
presents a token whose `sha256` matches** (`GET /api/visitor/[token]`), and it has **no way to
bind the token-holder to the recognised person**: door-api derives the name from face
recognition *at the door*, while the token is simply whatever phone scanned the wallboard QR.
A different person who scans that session's QR therefore receives the resident's real name.
Worse, under the hostile-courier model (ADR-0016 §9) the name then also sits in the relay's
store, where a relay compromise reads it — exactly the "no enrolled person's `display_name`
crosses to the relay" line ADR-0017 §2 drew before ADR-0018 carved its exception.

The owner's decision (2026-08-25): **disclose that a write will be attributed, without printing
the name on the public relay page.** The LAN surfaces — the wallboard and the door-ui visitor
page, both reachable only from the house — keep the name, because there the person reading is
the person at the door.

## Decision

**The name never crosses to the relay.** On the relay path, `attributed_to: str | None` becomes
`attributed: bool` in both relay snapshot contracts — `VisitorSessionSnapshot` (what door-api
pushes) and `VisitorPublicSnapshot` (what the relay serves). door-api sends
`attributed = (attributed_display_name() is not None)` — true only when a recognised person's
consent covers attribution, exactly as the name was gated before. The relay stores and serves
the boolean. The visitor page, when `attributed` is true, discloses generically — "the door
recognises you, so your note will be posted under your name" — with no name to print.

This **restores ADR-0017 §2's original invariant** (no enrolled person's `display_name` reaches
the relay) while keeping ADR-0018 E-23's substance: attribution is still disclosed before the
write, never silent. E-23 is satisfied by the disclosure, not by naming.

**The LAN path is unchanged.** door-api's `/session` (wallboard) and `/visitor-session`
(door-ui visitor page) still return `attributed_to` (the name). Those surfaces are house-only
and were never the leak.

### Enforcement

- **E-26 The relay never receives a display name.** No relay-facing contract model has a field
  capable of carrying a name; `attributed` is a boolean. A relay snapshot model with a name
  field is a review-blocking defect — the same rule ADR-0017 §2 set, with ADR-0018's exception
  withdrawn from the relay path.
- **E-23 still holds, reframed:** any surface that attaches identity to a write discloses it
  before the write. On the relay the disclosure is name-free; on the LAN it may name.

## Test specification

| ID | Test | Where |
|---|---|---|
| P-34 | `test_relay_snapshot_carries_no_name` — a push built while a person is recognised (consent covering attribution) sets `attributed=true` and contains no `attributed_to`/`display_name`/name string anywhere; unrecognised → `attributed=false`. Sentinel-scanned. | door-api / contracts |
| P-35 | `test_visitor_page_discloses_attribution_without_a_name` — with `attributed=true` the page shows the disclosure and no personal name; with `false`, neither. | public-relay |

The existing P-20 (`test_snapshot_carries_no_private_fields`) strengthens: the relay snapshot now
contains no name under any condition.

## Consequences

- `packages/contracts` changes (`VisitorSessionSnapshot`/`VisitorPublicSnapshot`:
  `attributed_to` → `attributed`); schemas and TS regenerated. This is a coordinated
  door-api + relay deploy, but the field is additive-shaped (a boolean default `false`), so an
  old relay reading a new push simply never shows a name — fail-safe.
- door-api's LAN endpoints and door-ui's LAN visitor page are untouched; the name still shows
  on the house wallboard/visitor page as ADR-0018 intended.
- Independent of ADR-0043 (the Cloudflare move): this changes *what* crosses, that ADR changes
  *where* the relay runs. They compose but do not depend on each other.
