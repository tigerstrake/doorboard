# control-plane-api — trusted control plane

**Plane:** control · **Host:** NUC · **Language:** Python · **Tasks:** T-501, T-504

The trusted aggregation point. Receives the door plane's event stream, owns durable history in PostgreSQL, bridges Home Assistant, computes presence status, hosts social-feature storage/moderation, distributes config to the Pi, and sends notifications.

## Responsibilities

- **Event ingestion:** authenticated endpoint for door-sync; events land in Postgres (append-only tables keyed by `event_id`, idempotent).
- **Presence/Weasley clock:** compute `status.presence_changed` with the fixed precedence — manual > focus shortcut > geofence label > calendar > default. Broad labels only; sources arrive via HA webhooks/phone shortcuts. Manual override always wins.
- **Home Assistant bridge:** consume/emit via Mosquitto and the HA API with a scoped token; HA automations never gate door behavior.
- **Social storage:** guestbook/poll/scoreboard/mood durable storage, moderation and deletion endpoints honoring `social.deletion_requested`.
- **Config distribution:** versioned config bundles the Pi pulls (never pushes secrets to the Pi beyond its limited scope).
- **Notifications:** owner pings (bell missed, storage alerts, sync failures) via HA/ntfy-style channel chosen in the brief.

## Must never

Sit in the door critical path; expose admin surfaces without auth; store raw biometrics (it never receives them by design); publish exact location or calendar contents to anything the wallboard can read.

## Interfaces

HTTP: `/health`, `/metrics`, `/ingest` (token-auth), `/status/*`, `/social/*`, `/config/door/{door_id}`. MQTT: `doorboard/#` topics for HA/audit fan-out. DB: PostgreSQL with Alembic migrations.

## T-501 status (this task)

Implements: Postgres schema + Alembic migrations, `/ingest` (idempotent by
`event_id`, per-batch results), Pi-scoped service tokens (issue/revoke,
`packages/auth`), `/config/door/{door_id}` (versioned bundle, checksum,
secret-free by construction — `packages/config`), social moderation/deletion
for guestbook and checkin content, person-data purge
(`DELETE /people/{person_id}/events`, ADR-0009 §3.4), MQTT audit fan-out, and
owner notifications.

**Out of scope for T-501** (see brief): the Home Assistant bridge and its
entities (T-503), presence computation / the Weasley clock (T-504), and
running this service inside the NUC's Docker Compose stack (T-503 wires that
up — this task only guarantees the service runs standalone against a
Postgres instance).

**Social storage scope.** Only `social.guestbook_entry_created` and
`social.checkin_created` get a durable, moderatable mirror (`social_items`)
— those are the only two `social.deletion_requested.target_kind` values the
contract defines outside of media/enrollment. `poll_vote_cast`,
`mood_updated`, and `scoreboard_updated` are still durably recorded (they
land in the append-only `events` table like everything else) but have no
moderation panel, since nothing in the contract makes them deletable
targets.

**Notification channel: ntfy** (the brief's other option, Home Assistant
notify, would pull in T-503's HA bridge as a dependency, which is explicitly
out of scope here). Set `NTFY_URL`/`NTFY_TOPIC` in `.env`; unset means
notifications just log instead of sending (dev/CI default). Rules: missed
bell (`session.ended{outcome=unanswered_timeout}`), critical storage
(`system.storage_alert{severity=critical}`), and sync falling behind
(`media.storage_status.oldest_unsynced_s` over
`CONTROL_PLANE_SYNC_STALL_ALERT_S`, default 4h) — each rate-limited by
`CONTROL_PLANE_NOTIFY_COOLDOWN_S` (default 1h) per rule+subject.

**Admin auth stopgap.** `packages/auth` doesn't have session-based admin
auth yet (see its README). `/admin/*` here is gated by
`CONTROL_PLANE_ADMIN_TOKEN` (shared bearer secret, `secrets.compare_digest`,
fails closed with 503 if unset) — the same pattern `door-api`'s social panel
uses. Replace both when real admin auth lands.

**Service token scopes:** `ingest` (batch event submission and the
person-data purge call — both are Pi-automation calling into the NUC),
`upload` (reserved for door-sync's media upload credential; door-sync itself
is T-502), `config` (read-only `/config/door/{door_id}` access). Issue/revoke
via `POST/DELETE /admin/tokens` or offline via
`uv run python -m control_plane_api.cli issue-token --door-id primary --scope ingest`.
Revocation takes effect on the very next request — verification always
re-reads the `service_tokens` table, never caches a decision.

**Person purge (ADR-0009 §3.4).** `DELETE /people/{person_id}/events`
deletes every archived event whose payload carried that `person_id`
(`vision.identity_stable`, `vision.identity_expired`,
`social.checkin_created`) and marks that person's checkin mirror rows
deleted. Idempotent and safe to retry — a repeat call with nothing left just
returns zero counts. `social.deletion_requested{target_kind="enrollment"}`
delegates to the same logic (`target_id` is the `person_id` in that case).

## T-504 status (presence / Weasley clock)

Implements the presence engine `T-501`'s README scoped out: per-subject
(`owner`, `roommate`, auto-provisioned) source registry
(`presence_sources`/`presence_subjects`, migration `0002`), fixed precedence
resolution (`manual > focus_shortcut > geofence_label > calendar > default`,
`presence.py`), `until`-expiry fallthrough recomputed on every read *and*
write (no background scheduler needed), change-only `status.presence_changed`
emission (`presence_engine.sync_presence` — a `presence_history` row and
MQTT publish happen only when the resolved (label, source, until) actually
changes), and a retention cap (`CONTROL_PLANE_PRESENCE_HISTORY_MAX_ROWS`,
default 500 rows per subject).

**Routes** (see `app.py`'s module docstring for the full list):
`GET /status/presence` / `GET|PATCH /status/presence/{subject_id}` /
`GET /status/presence/{subject_id}/history` (admin) for the admin panel;
`POST|DELETE /status/presence/{subject_id}/override` (admin UI + one-tap
phone shortcut) for the manual override that always wins instantly;
`PATCH /status/presence/{subject_id}/sources/{source}` (admin) for
per-source enable/disable; `POST /status/presence/webhook/focus-shortcut`
and `.../webhook/geofence-label` for HA-forwarded phone Focus
shortcuts/voluntary geofence labels (T-503 wires the HA→here forwarding;
this task only implements the receiving endpoint). All of the above reuse
`CONTROL_PLANE_ADMIN_TOKEN` rather than adding a new `ServiceTokenScope` —
see the app.py docstring for why that's still the right stopgap here.
`GET /status/presence/bundle` is Pi-facing (`config`-scoped token, same tier
as `/config/door/{door_id}`) — the data bundle the wallboard caches so its
presence tile can show a last-known label with a staleness hint
(`CONTROL_PLANE_PRESENCE_STALE_AFTER_S`, default 1800s) through a NUC
outage; see `presence.is_stale`.

**Privacy.** Every label passed to any presence endpoint is validated
against the fixed eight-label `PresenceLabel` enum — no ad hoc strings.
`focus_shortcut`/`geofence_label` webhook payloads are scanned recursively
for coordinate-shaped fields (`lat`, `lon`, `gps`, `geo`, `location`,
`coordinate`, at any nesting depth) and rejected with a logged
`presence_webhook_coordinate_payload_rejected` warning plus a 422 — on top
of the request model's `extra="forbid"`, which already rejects anything
outside `{subject_id, label, until}`. `tracking_enabled` (per subject) gates
the *inferred* sources (focus_shortcut, geofence_label, calendar) — the
"config flag per subject" the brief scopes roommate consent down to; manual
overrides are never gated by it, since a subject stating their own status
isn't the kind of inference consent is about.

**Calendar inference is a stub** (`CalendarProvider` protocol +
`MockCalendarProvider`, wired into `AppState.calendar_provider`) — it always
returns "no signal" until a real provider is injected; real calendar wiring
is an explicitly later brief.

**Out of scope for T-504** (see brief): real calendar integration, phone
Focus-shortcut authoring (documented for the owner, not automated), roommate
consent flows beyond the `tracking_enabled` flag, and wallboard tile visuals
(already exist — this task only had to make the data available).

### Running tests locally

Tests run against a real local Postgres — not SQLite — because the
idempotency guarantee this service provides depends on the actual unique-
constraint/`IntegrityError` behavior of the production database engine.

```bash
brew install postgresql@16
brew services start postgresql@16   # or run postgres manually
createuser -s doorboard
createdb -O doorboard doorboard_test
psql -d doorboard_test -c "ALTER USER doorboard WITH PASSWORD 'doorboard';"
```

Override `CONTROL_PLANE_TEST_DATABASE_URL` if you use different credentials.
CI runs an equivalent Postgres service container (see `.github/workflows/ci.yml`).
