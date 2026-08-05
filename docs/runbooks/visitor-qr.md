# Runbook: the visitor QR

Operating the wallboard QR that visitors scan (ADR-0017). Covers door-api's relay client and the visitor routes on [apps/public-relay](../../apps/public-relay/).

**First thing to know:** the QR is not in the door path. The button, the ESP32, ringing, the wallboard, recording, and the doorpad all work identically whether the relay is up, down, or deleted. If someone reports the *door* misbehaving, this is not your suspect.

**Second thing:** there are two QR targets, and knowing which one is live explains most symptoms.

```bash
curl -s http://<door>:8080/visitor-relay-status
# {"configured":true,"status":"ok","qr_target":"relay",...}
```

| `qr_target` | The QR encodes | Works for |
|---|---|---|
| `relay` | `https://<deployment>/v/<token>` | Any phone, any network |
| `lan` | `http://door.local/visitor?token=…` | Only phones on the house wifi |

door-api picks this per QR render: `relay` only when an exchange succeeded within `DOOR_API_VISITOR_RELAY_FRESHNESS_S` (default 30 s), otherwise `lan`. That fallback is deliberate — an internet outage degrades the QR to LAN-only rather than killing it.

## Normal operation

1. Someone rings. The wallboard enters visitor mode and shows a QR.
2. door-api mints a short-lived signed token and pushes a snapshot — ring state, current poll, `sha256(token)` — to the relay.
3. The visitor scans, and their phone reads that snapshot. Ring status updates within ~2 s.
4. They leave a note or vote. It queues at the relay; door-api collects it on its next poll and applies it through the normal social path.
5. The note appears in the guestbook and, once approved, on the wallboard.

## Symptoms

### The QR gives "Link not valid" or "Session ended"

The relay has no snapshot matching that token.

- **Most likely benign:** the visitor token expired (`DOOR_API_VISITOR_TOKEN_TTL_S`, default 300 s) or the session ended. The QR is per-session and short-lived by design. Ring again.
- Snapshot never pushed: check `qr_target`. If it says `relay` but the visitor page 404s, the push is failing — look at `polls_failed` and `last_error` in `/visitor-relay-status`.

### The QR points at door.local and visitors on cellular can't load it

`qr_target: "lan"`. That is the fallback doing its job; the question is why.

1. `{"configured": false}` → `DOOR_API_VISITOR_RELAY_BASE_URL` or `DOOR_API_VISITOR_RELAY_DEVICE_TOKEN` is unset. Set both, restart door-api.
2. `status: "degraded"` with `last_error` → door-api cannot reach the relay. Check egress and DNS, then `curl -sS https://<deployment>/api/health` **from the Pi**.
3. Repeated failures with the relay logging 401s → token mismatch. Compare `RELAY_VISITOR_DEVICE_TOKEN` (Vercel) with `DOOR_API_VISITOR_RELAY_DEVICE_TOKEN` (Pi). Note these are **not** the same as the enrollment pair; using the enrollment token here fails by design (E-17).
4. `storage_not_configured` from the relay → the Upstash store is not attached to the Vercel project.

### A note was "sent" but never appeared

The write queued but was not applied. The phone shows a reason; the mapping:

| Reason | Meaning | Fix |
|---|---|---|
| `rate_limited` | The social write limit refused it | Expected under spam; wait it out |
| `rejected_content` | The sanitiser refused it | Reword; oversized or disallowed markup |
| `poll_closed` / `already_voted` | Vote arrived late or twice | Nothing to fix |
| `session_mismatch` | The session ended before collection | Ring again |
| `not_found` | Poll or target no longer exists | Nothing to fix |
| `door_error` | Unmapped failure | Check door-api logs for `visitor_relay_action_rejected` |

Notes land as **pending** guestbook entries, exactly like the LAN path — so "it did not appear on the wallboard" is usually moderation, not delivery. Check the admin guestbook queue before investigating the relay.

### Ring status on the phone lags

Expected: it is a ~2 s poll of a pushed snapshot, not a live socket. If it lags much more, `polls_failed` is climbing.

## Turning the remote path off

Unset `DOOR_API_VISITOR_RELAY_BASE_URL` and restart door-api. The worker never starts, no egress happens, and the QR reverts permanently to the LAN URL — exactly the pre-ADR-0017 behaviour. Visitors on the house wifi are unaffected; visitors on cellular go back to not being able to load it.

## What the relay knows

Ring state, the current poll and its results, `sha256(session token)`, and visitor notes and votes — all of it content already shown on a wallboard in a shared hallway (ADR-0017). Snapshots live for the session plus 5 minutes; queued writes for 15 minutes.

It does **not** hold: any enrolled person's name or `person_id`, any recognition event, any media, any admin or diagnostic state, or a usable visitor token. If you are asked "could someone read our door's data from that website", that list is the answer.

## Deleting something a visitor left

Same as any other guestbook entry — admin panel, or the visitor's own deletion request, which routes through the identical path. The relay holds nothing to delete beyond a short-lived queue entry that expires on its own.
