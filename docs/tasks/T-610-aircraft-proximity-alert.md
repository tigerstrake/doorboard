# T-610: Aircraft proximity push-notification

**Agent:** claude · **Milestone:** M7 · **Depends on:** T-603, T-501, T-609

Adds an owner push-notification when a plane is within a configurable radius of
the aircraft observer centre. Reuses the T-609 Telegram channel ([ADR-0012](../adr/0012-telegram-video-message-delivery.md)).

## Context

The aircraft integration (T-603) emits `ambient.aircraft_summary` with every
plane in a wide bounding box and its ground distance from the observer centre.
Today the only proximity signal is a transient on-screen banner on the wallboard
(`VITE_AIRCRAFT_ALERT_DISTANCE_KM`) — there is no notification to the owner's
phone. The owner wants a push when a plane is within ~1 mile of home, while the
dedicated aircraft page keeps its wide view. Notifications live on the NUC
(T-501); the radius filter is a pure function of the summary the NUC already
ingests, so no new event or contract is needed.

## Deliverables

- An `ambient.aircraft_summary` rule in `control_plane_api.notify.evaluate_rules`
  that fires when a plane is within `AIRCRAFT_ALERT_RADIUS_MI` of the observer
  centre (measured from the `distance_km` the summary carries), with an optional
  altitude ceiling and its own cooldown (via a per-`Notification` cooldown override).
- A Telegram owner-notification channel (`TelegramNotifier`) + `MultiNotifier`, so
  owner notifications (this alert, missed-bell, storage, sync) reach ntfy and/or
  Telegram — reusing the T-609 bot client.
- Settings + `.env.example`: `AIRCRAFT_ALERT_RADIUS_MI`,
  `AIRCRAFT_ALERT_MAX_ALTITUDE_FT`, `AIRCRAFT_ALERT_COOLDOWN_S`, and the correct
  `AIRCRAFT_OBSERVER_LAT/LON` centre var.
- Unit tests for the rule (radius, altitude, disabled, nearest/count, per-alert
  cooldown) and channel routing.

## Out of scope

- Changing the dedicated aircraft page or its bounding box (stays wide).
- The existing on-screen wallboard banner (`VITE_AIRCRAFT_ALERT_DISTANCE_KM`).
- Independent alert centre ≠ page centre (the summary carries distance only, not
  per-plane lat/lon, so the alert is measured from the page centre).
- Fixing unrelated `.env.example` drift (OpenSky auth naming, satellite observer
  var names) — flagged separately.
- Any `packages/contracts` change; any door-Pi-side change.

## Acceptance criteria

- A plane within the radius (and under the altitude ceiling, if set) produces one
  "Plane overhead" notification naming the nearest aircraft; planes outside the
  radius or above the ceiling do not.
- `AIRCRAFT_ALERT_RADIUS_MI=0` disables the alert; the page is unaffected either way.
- The alert honours its own cooldown independent of the global notify cooldown.
- Notifications are delivered to every configured channel; a channel failure
  never blocks ingestion.
- `scripts/lint`, `scripts/typecheck`, and `scripts/test` pass.
