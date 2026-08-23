# ADR-0042: an admin unlock on the DoorPad, surfaced by recognition, gated by a token

**Status:** Accepted · **Date:** 2026-08-23 · **Amends:** handoff §4.4 / §12 ("Small DoorPad … No administrative access"), ARCHITECTURE.md §3 (door-ui row) · **Upholds:** ADR-0005 §3 (recognition is never authorization)

## Context

The owner wants to reach the admin console **from the DoorPad itself** — walk up, be
recognised, get in — instead of only from a laptop on the LAN. The handoff explicitly said
the DoorPad has no administrative access, because it is a public, corridor-facing surface.
And there is a hard invariant that must not bend to make this convenient: **recognition
personalises, it never authorises** (ADR-0005 §3). A door that unlocked its admin console
because it *saw a face* would be a door anyone could open with a printed photo.

## Decision

Add an admin entrance on the DoorPad with two ways in, both of which land on the **existing
`/admin` token gate** — the token is the only thing that authorises:

1. **Recognition *surfaces* it.** When the door currently holds a recognised identity, a small
   "Admin" button appears next to the recognised-name badge. It only *navigates* to `/admin`;
   it grants nothing.
2. **A long-press bootstrap.** Pressing the DoorPad title for ~2.5 s also opens `/admin`. This
   works even when recognition is down — which is exactly the state you are in when you need to
   re-enroll a stale face — so admin can never be locked out of its own door.

**The anti-spoofing the owner asked for is the token, not the face.** Recognition decides
whether the *entrance is visible*; the admin token decides whether it *opens*. A face — live or
a photo held to the camera — reveals the button and nothing more. This is the only design that
gives the owner what they asked for without turning recognition into authorization: the two
are kept strictly separate (surface vs. authorise), so ADR-0005 §3 still holds in full.

## Consequences

- The DoorPad can now display the admin console once the token is entered. It faces a shared
  corridor, so this is a genuinely higher-exposure admin surface than the handoff intended:
  the token must be entered where it can be shoulder-surfed, and an unlocked console left on
  the pad is readable by a passer-by. Mitigations that follow from this and should be kept:
  the button is shown only to a recognised person (low discoverability), the token is never
  displayed, and the console should auto-lock on inactivity (a follow-up if the current admin
  lock is not aggressive enough on this surface).
- No new authorization path exists: every admin action still calls the same token-checked
  control-plane/door-api routes. Recognition reaching an *access* decision remains forbidden.
- This is the intended way to fix a stale enrollment on the spot: long-press → token →
  re-enroll, without needing another device.
