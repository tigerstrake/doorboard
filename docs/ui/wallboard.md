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
| Printer | state/progress, optional thumbnail | `ambient.printer_status` |
| Mood | current mood check-in | `social.mood_updated` |
| Scoreboard | casual roommate board | `social.scoreboard_updated` |
| Food | daily recommendation | `ambient.food_recommendation` |
| Poll | current poll + results | door-api local |
| Guestbook | *selected/approved* highlights only | door-api local (moderated) |
| About | project summary + fun facts (lines of code, languages, structural counts) | static build-time facts (`tools/project-stats`, T-608) |

Every ambient tile shows an `as_of` staleness hint when data is old (NUC-outage fallback uses last-cached bundle). Layout responsive portrait/landscape.

## Visitor mode

Greeting (generic, or profile-colored/personalized for enrolled visitors), broad availability state, ring/video-message instructions, QR code (tokenized `/visitor` link), poll prompt, celebratory visual effects.

## Forbidden on this screen (privacy — enforced in review)

Exact location, calendar contents, private notes, full visitor logs, diagnostics, private photos, unenrolled people's names, raw camera feed by default.
