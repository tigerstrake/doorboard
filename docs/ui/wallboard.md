# UI spec — Wallboard (`/wallboard`)

Public 21.5–24" display. Two modes; visitor mode takes over on `session.state_changed → VISITOR_MODE` and returns to ambient on `SESSION_END` (target: bell → visitor mode p95 < 250 ms, driven entirely by Pi-local WebSocket).

## Ambient mode — tile dashboard

| Tile | Data | Source event |
|---|---|---|
| Presence (owner + roommate) | broad label + color/icon, Weasley-clock styling | `status.presence_changed` (cached locally) |
| Clock | local time | local |
| Birds | today's top species/counts | `ambient.bird_summary` |
| Aircraft | nearby summary | `ambient.aircraft_summary` |
| Next satellite pass | ISS/selected pass, rise time, direction | `ambient.satellite_pass` |
| Printer | state/progress/ETA (no camera or thumbnail — the payload carries none) | `ambient.printer_status` |
| Mood | current mood check-in | `social.mood_updated` |
| Scoreboard | casual roommate board | `social.scoreboard_updated` |
| Food | daily recommendation | `ambient.food_recommendation` |
| Poll | current poll + results | door-api local |
| Guestbook | *selected/approved* highlights only | door-api local (moderated) |
| About | project summary + fun facts (lines of code, languages, structural counts) | static build-time facts (`tools/project-stats`, T-608) |

Every ambient tile shows an `as_of` staleness hint when data is old (NUC-outage fallback uses last-cached bundle). Layout responsive portrait/landscape.

## Visitor mode

Greeting (generic, or profile-colored/personalized for enrolled visitors), broad availability state, ring/video-message instructions, QR code (tokenized `/visitor` link), poll prompt, celebratory visual effects.

## Body Surfers (ADR-0049)

Started from the DoorPad, the game covers the whole wallboard in a sandboxed iframe framed from the other loopback name (`127.0.0.1` ↔ `localhost`), so it runs in its own renderer process. Any visitor takeover state, privacy mode, the game's own exit (idle lobby, nobody in view, error) or the 15-minute cap removes the frame in the same render, and a game never resumes after a visit. The game is silent and needs no pointer: you join by raising both hands.

## Forbidden on this screen (privacy — enforced in review)

Exact location, calendar contents, private notes, full visitor logs, diagnostics, private photos, unenrolled people's names, raw camera feed by default.

The one exception is the Body Surfers crash-photo leaderboard (ADR-0049 §6): the photo is a face crop of a player who joined a run after being told about it, and it is shown only on the game's own screens. It is never shown on ambient tiles and is never uploaded. It is kept for 30 days, and the owner can clear it.
