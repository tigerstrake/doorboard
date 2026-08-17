# ADR-0024: The admin page reaches loopback services through door-api

**Status:** Accepted · **Date:** 2026-08-17

Applies the pattern from [ADR-0019](0019-self-service-enrollment.md)
(§"Credentials do not move to the door") and [ADR-0022](0022-ring-photo-notification.md)
to the owner's admin surface. Changes no binding: ARCHITECTURE.md §2 keeps door-visiond and
door-media off the LAN, and they stay there.

## Context

Opening `/admin` from a laptop showed a page of failures — video unavailable, storage
unavailable, "Message inbox unavailable", "Couldn't load the moderation panel", "Load
failed" under Face Recognition, Arrival log and phone enrollment.

Two independent causes, neither described by the errors on screen.

**The token.** The sign-in said "Enter the Pi-local admin token". The door has three
48-character admin tokens — `DOOR_API_SOCIAL_ADMIN_TOKEN`, `DOOR_VISIOND_ADMIN_TOKEN`,
`DOOR_MEDIA_ADMIN_TOKEN` — and this page needs the first. Entering another returns 401 on
every door-api admin call, which the panels rendered as emptiness: "no recordings" while
the API returned recordings, "0 pending" while it returned entries.

**The ports.** The enrollment, arrival-log, invite, privacy and camera panels called
door-visiond `:8081` and door-media `:8082` *directly from the browser*, and one panel had a
hardcoded `http://<host>:8081/health`. Those services bind `127.0.0.1`; only door-api binds
`0.0.0.0`. So those panels worked from the Pi's own browser and could never work from
anywhere else.

The second is the serious one, because of *how* it failed. `Promise.all` rejected and the
panels fell back to their empty states, so the page asserted things that were false:
**"Enrolled Members (0)" for a door with two people enrolled**, and "Relay not configured"
for a relay reporting `relay_status: ok`. An error invites investigation; a wrong fact does
not.

## Decision

**door-api proxies them, at `/admin/visiond/*` and `/admin/door-media/*`.** It is already
LAN-exposed and admin-authenticated, and it holds each service's own token server-side. The
browser presents door-api's token and receives the data; it never receives — and cannot
derive — the service credential. That is exactly ADR-0019's "the capability is exposed, the
admin API is not", generalised.

**Allow-listed per method and path, not open forwarding.** A wildcard
`/admin/visiond/{path}` forwarder would make door-api's admin token a skeleton key for every
door-visiond route, including ones added later by someone who never considered this proxy.
Each entry is a decision that the owner's browser may invoke it. Patterns are anchored,
because unanchored patterns are how allow-lists leak.

Deliberately excluded:

- **`POST /enroll`** — multipart face images. Enrollment belongs to the at-door and
  phone-relay flows (ADR-0016/0019); routing raw biometric payloads through a second,
  LAN-exposed service buys nothing.
- **`/metrics`**, and every verb not listed.

`GET /health` *is* included: the enrollment panel needs `privacy_enabled` for the
recognition toggle, and behind admin auth the owner seeing their own door's health is the
purpose of the page.

**Rejected: binding door-visiond and door-media to `0.0.0.0`.** One line, everything works,
and it puts the biometric enrollment API and the media API on the house network — which
ARCHITECTURE.md §2 forbids and ADR-0009 exists to prevent. The convenience is real and the
trade is not close.

**The sign-in names its variable**, and shows the one-line command that prints it. Three
similarly-shaped secrets with one unlabelled field is a trap regardless of the proxy.

## Consequences

- door-api gains a proxy surface. It is admin-authenticated, allow-listed, and tested
  against path traversal, wrong verbs and the skeleton-key case.
- A failure now names the service (`503 door-visiond unavailable`) instead of drawing an
  empty list, which was the actual defect.
- **The live camera panel still only works on the Pi.** WebRTC needs a direct peer
  connection to MediaMTX on `:8889`; that cannot be proxied by forwarding HTTP, and
  exposing it would defeat the point. Off-Pi it stays "video unavailable" — honestly this
  time, and the recordings and still both work.
- One more place to remember when adding a door-visiond route the admin page should see.
  That is the cost of the allow-list, and it is the point of it.
