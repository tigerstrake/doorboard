# ADR-0017: The visitor surface moves behind the public relay

**Status:** Accepted · **Date:** 2026-08-05 · **Extends:** ADR-0016 (relay pattern), ADR-0005 §5 (public surfaces) · **Amends:** ARCHITECTURE.md §2 (trust table), §10 (failure behaviour)

Binding on T-307. Mechanisms continue ADR-0016's numbering (E-15…E-20) and tests continue its (P-20…P-25).

## Context

The wallboard shows a QR code when someone rings. It encodes `visitor_public_base_url` + a signed token, and that setting defaults to `http://door.local`.

A visitor standing at the door has their phone on cellular. `door.local` is mDNS on the house LAN, so **the page does not load at all** for the exact person it exists for. The feature works only for someone already on the house wifi — which a stranger at the door is not. This is a defect, not a preference: the QR has been decorative in its primary case.

The relay pattern from ADR-0016 already solves the shape of this problem (public site, outbound-only Pi, no inbound exposure). What differs is the data.

## The data is public by design, and that changes the argument

Enrollment needed end-to-end encryption because photos and names are biometric-adjacent PII. The visitor surface is the opposite: a guestbook note and a poll vote exist **in order to be displayed on a wallboard in a shared hallway**. Encrypting them end-to-end would be security theatre — the wallboard shows them to passers-by seconds later.

ARCHITECTURE.md §2 already classifies QR/PWA visitor endpoints as **Low** trust with the controls that follow from it: *tokenized, rate-limited, short-lived*. This ADR keeps exactly those and does not pretend to more.

So, stated plainly rather than buried: **the relay sees visitor note text, poll choices, ring state, and timing.** That is acceptable because all of it is already destined for a public screen. What makes this safe is not secrecy but **scope** — and scope is what the mechanisms below enforce.

### What must never cross, and why that is the real invariant

| Never sent to the relay | Why |
|---|---|
| Any enrolled person's `display_name`, `person_id`, or profile | Recognition data is not public content (ADR-0005 §8) |
| Any `vision.*` event or field | The contract firewall (ADR-0009 E-4) does not get a second door |
| Admin state, diagnostics, storage/health detail, token material | Public surfaces show broad status only (ADR-0005 §5) |
| Media: clips, thumbnails, photos, video messages | Visitor-initiated media stays on the Pi→NAS path (ADR-0007) |
| The visitor session token itself | Only `sha256(token)` is pushed (E-16) |

A snapshot field that is not on the allow-list in §2 below cannot be added without a superseding ADR. That is the whole security model of this change: a narrow, enumerated, allow-listed projection of public content — not a general window into door-api.

## Decision

```text
phone (cellular)              relay (Vercel)                 door-api (Pi)
GET /v/<token>      ──────▶   serves the page
GET  snapshot       ──────▶   cached snapshot  ◀────── PUT on every state transition
POST note/vote      ──────▶   queued action    ──────▶ collected on outbound poll (~2 s)
                              outcome          ◀────── ack: created id / poll results
```

Reads are a snapshot the Pi pushes; writes are queued and collected. Every exchange is initiated by the Pi. No inbound port, no tunnel, works behind NAT — identical to ADR-0016 §6.

### 1. Session identity without holding a credential

The visitor token stays an HMAC blob only door-api can verify (`visitor_tokens.py`, unchanged). door-api pushes `sha256(token)` with the snapshot; the relay authorises a phone by hashing what it presents and comparing. **The relay therefore cannot mint or replay a visitor token**, and a dump of its store yields no usable credential (E-16).

### 2. Snapshot allow-list (binding)

```
session_token_sha256   base64url        — authorises the phone; not a credential itself
session_id             opaque uuid
state                  SessionState     — the eight session states, nothing derived
expires_at             UTC
poll                   {id, question, options:[{id,label}]} | null
poll_results           [{option_id, votes}] | null
note_accepted          {action_id, entry_id} | null   — outcome echo for the phone
```

Nothing else. No visitor name, no recognition, no media, no counts of anything private.

### 3. Queued action allow-list (binding)

`note{text}`, `vote{poll_id, option_id}`, `deletion_request{kind, target_id}` — the three things the existing visitor page does, and nothing more. Sizes bounded at the relay and re-bounded at door-api; the existing guestbook sanitiser and rate limits stay the authority on content (they already defend the LAN path).

### 4. An internet outage must not kill the QR

Today the QR works with the internet down (it is LAN-only). Moving it to the relay would regress that, so:

**door-api chooses the URL at QR-render time.** Relay known-reachable → relay URL. Otherwise → the LAN URL, exactly as today. A visitor on the house wifi keeps working through any relay outage; a visitor on cellular during an outage cannot be served by anything, which is unavoidable and is not a regression from a working state.

This keeps ARCHITECTURE.md §10 honest: internet offline still has no impact on the core flow, and now a *documented* partial impact on the visitor QR rather than an undocumented total one.

### 5. Scoped device tokens

door-api and door-visiond are separate services with different blast radii, so they get different credentials:

| Token | Held by | May call |
|---|---|---|
| `RELAY_DEVICE_TOKEN` | door-visiond | door-key, invite, enrollment pickup/ack |
| `RELAY_VISITOR_DEVICE_TOKEN` | door-api | visitor snapshot, visitor pickup/ack |

A stolen door-api token cannot collect enrollment ciphertext, and a stolen door-visiond token cannot post visitor content. Neither can read the other's routes (E-17).

## Enforcement mechanisms

- **E-15 Snapshot projection is allow-listed.** door-api builds the snapshot by naming each field (§2); it never serialises a session/machine object wholesale. A contracts model with `extra="forbid"` and no field capable of carrying identity, media, or diagnostics is the second gate.
- **E-16 No usable credential on the relay.** Only `sha256(token)` crosses. The relay has no HMAC secret and no route that issues a token.
- **E-17 Per-service scoped device tokens.** §5. Each route checks exactly one scope; a route accepting either is a review-blocking defect.
- **E-18 Content authority stays on the Pi.** The relay applies size caps and rate limits, then passes text through untouched. Sanitisation, moderation, and the social rate limits remain door-api's existing code — the relay is not a second, weaker validator that could diverge from it.
- **E-19 Fail-safe URL selection.** The QR encodes the relay URL only when door-api has *observed* the relay reachable within a bounded window; unknown or stale means LAN. Failure direction is toward the thing that works offline.
- **E-20 Visitor routes reach nothing else.** The visitor scope cannot touch enrollment routes, and no relay route can query door-api. Queued actions are collected, never pushed.

## Test specification (binding for T-307)

| ID | Test | Where |
|---|---|---|
| P-20 | `test_snapshot_carries_no_private_fields` — build a snapshot while a person is recognised, media exists, and admin state is populated; assert the serialised snapshot contains no `display_name`, `person_id`, vision field, media path, or diagnostic key. Sentinel-scanned, not eyeballed. | door-api |
| P-21 | `test_relay_never_holds_a_usable_visitor_token` — drive a full visitor flow; byte-scan everything the relay stored for the token string → absent, while `sha256(token)` is present and authorisation still works. | public-relay |
| P-22 | `test_device_token_scopes_are_disjoint` — the visitor token is rejected on every enrollment route and vice versa. | public-relay |
| P-23 | `test_queued_actions_apply_once` — a note and a vote collected twice (duplicate delivery after a missed ack) create exactly one guestbook entry and one vote. | door-api |
| P-24 | `test_qr_falls_back_to_lan_when_relay_unreachable` — relay unobserved/stale → the QR URL is the LAN base; relay healthy → the relay base. Both are valid absolute URLs. | door-api |
| P-25 | `test_visitor_relay_outage_never_blocks_the_session` — relay hanging past the timeout for a whole run: button → ring → answer/timeout completes normally, session state machine timings unaffected. | door-api |

## Consequences

- door-api gains outbound internet egress to one origin — its first. Recorded in the security checklist alongside door-visiond's.
- `apps/public-relay` becomes `apps/public-relay`: it now serves two unrelated public surfaces and the old name would misdescribe it. The Vercel project is renamed to match, which changes the production URL.
- The visitor page exists twice on purpose: door-ui's for the LAN fallback, the relay's for the public path. They are not shared code, because one talks to door-api and the other to the relay. The duplication is bounded (~200 lines of small, stable UI) and is the price of not regressing offline behaviour.
- Nothing about the door interaction path changes. Recognition, the button, the ESP32, recording, and at-door enrollment are untouched.
