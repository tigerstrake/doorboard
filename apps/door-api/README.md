# door-api — visitor/session orchestration

**Plane:** real-time door · **Host:** door Pi 5 · **Language:** Python · **Tasks:** T-401, T-403 (state machine: Codex; social CRUD: Gemini)

The local brain of the visitor experience. Owns the visitor session state machine, consumes ESP32 button/knock events, coordinates displays via WebSocket broadcasts, serves DoorPad/visitor data, and enforces privacy mode.

## Owns

- **Session state machine** (normative states in [events.md §session](../../docs/protocols/events.md)); persisted in SQLite so sessions survive service restarts and kiosk reloads; auto-expiry back to IDLE.
- **Button/knock handling:** translate `door.button_pressed` into immediate `VISITOR_MODE` transition and display broadcast — target bell → visitor mode on wallboard p95 < 250 ms, all local.
- **Display state:** WebSocket fan-out to door-ui kiosks (snapshot on connect, then deltas).
- **Public interaction data:** guestbook, polls, check-ins — sanitized, rate-limited, deletable; stored locally, mirrored to control plane via door-sync when reachable.
- **Privacy mode:** propagates to door-visiond and ESP32; basic door interaction keeps working.
- **Tokenized visitor endpoints:** QR-code flows with short-lived signed tokens, rate-limited (api-conventions.md).
- **Local fallback content:** wallboard data cache so ambient tiles show last-known data when the NUC is down.
- **Photo booth coordination:** when `FEATURE_PHOTOBOOTH=true`, DoorPad explicit still-capture requests proxy to door-media, admin gallery actions proxy to door-sync, and `/wallboard/moments` returns only owner-approved gallery moments.

## The visitor QR works off the house wifi (ADR-0017)

The QR encodes `DOOR_API_VISITOR_PUBLIC_BASE_URL`, which defaults to `http://door.local` — an mDNS name a stranger's phone on cellular cannot resolve. So the page it points at could not load for the exact person it exists for.

The fix is the same courier pattern door-visiond uses for enrollment: this service pushes a narrow snapshot of **public** session state to [apps/public-relay](../public-relay/) and collects queued visitor writes on an outbound poll. No inbound port, no tunnel.

```text
phone (cellular) ──▶ relay ◀── this service (outbound poll, ~2 s)
   reads ring status + current poll from the pushed snapshot
   writes note / vote / deletion request → queued → applied here
```

What crosses is deliberately narrow — ADR-0017 §2 lists eight fields and nothing else may be added without a superseding ADR. No enrolled person's name, no `person_id`, no vision event, no media, no admin or diagnostic state. Collected writes go through the existing `SocialService`, so the sanitiser and rate limits are the same ones the LAN path uses.

**An internet outage does not kill the QR.** The URL is chosen at render time: the relay only when a successful exchange happened inside `DOOR_API_VISITOR_RELAY_FRESHNESS_S`, otherwise the LAN URL exactly as before. `GET /visitor-relay-status` reports which target is live.

| Variable | Meaning |
|---|---|
| `DOOR_API_VISITOR_RELAY_BASE_URL` | Relay origin. Empty (default) → no worker, no egress, QR behaves as it always did. |
| `DOOR_API_VISITOR_RELAY_DEVICE_TOKEN` | Visitor-scope bearer token. Distinct from door-visiond's, so neither service can use the other's routes. |
| `DOOR_API_VISITOR_RELAY_PUBLIC_URL` | Origin used to build the QR link, if it differs from the API base. |
| `DOOR_API_VISITOR_RELAY_POLL_S` | Collection cadence (default 2 s). |
| `DOOR_API_VISITOR_RELAY_FRESHNESS_S` | How long a success keeps the relay advertised in the QR (default 30 s). |

Outbound HTTPS to that one origin is the only internet egress this service has.

## Must never

Wait on control-plane HTTP for any session transition; show unsanitized user content; expose admin actions without auth; pretend an ESP32 effect happened when the controller is offline (surface an admin error instead); publish identity, media, or diagnostics to the public relay.

## Interfaces

Events in: `door.*`, `vision.identity_stable/_expired`, `media.*`. Events out: `session.*`, `social.*`. HTTP: `/health`, `/metrics`, `/ws`, session/guestbook/poll/checkin routes per task briefs, `/admin/*` (authenticated), and feature-gated photo booth/gallery routes.
