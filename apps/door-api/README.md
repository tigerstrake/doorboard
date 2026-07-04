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

## Must never

Wait on control-plane HTTP for any session transition; show unsanitized user content; expose admin actions without auth; pretend an ESP32 effect happened when the controller is offline (surface an admin error instead).

## Interfaces

Events in: `door.*`, `vision.identity_stable/_expired`, `media.*`. Events out: `session.*`, `social.*`. HTTP: `/health`, `/metrics`, `/ws`, session/guestbook/poll/checkin routes per task briefs, `/admin/*` (authenticated).
