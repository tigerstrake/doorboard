# ADR-0018: Recognised-identity personalisation — greeting, visit log, attribution

**Status:** Accepted · **Date:** 2026-08-05 · **Extends:** ADR-0005 (privacy model) · **Amends:** ADR-0009 §1 (enrollment schema), consent statement to v3

Binding on T-308. Mechanisms continue at **E-21**, tests at **P-26**.

## Context

Recognition currently ends at the ESP32: a stable match pushes a `profile_update` so the light animation plays, and the session machine records who arrived — but the screen says nothing until someone rings, nothing is logged, and nothing a recognised person does afterwards carries their name.

The owner wants recognition to actually mean something: greeted by name on approach, arrivals logged, interactions attributed, and the ring notification naming who is there.

## This is a purpose expansion, not a feature

Consent **v2** says, in the words every enrollee reads:

> Recognition is used **only to personalize** the door's greeting, lights, and sounds.

A durable log of when each person arrived is not personalisation. Neither is attaching someone's name to content they wrote, nor sending that name to Telegram. `consent_version` exists precisely to gate this, so the statement goes to **v3** and says plainly what now happens.

At the time of writing `enrolled: 0`, so nobody needs re-consenting. Had anyone been enrolled at v2, the honest handling would have been to withhold the new behaviours from them until they re-consented, not to apply them retroactively — noted here in case this recurs.

## Decision

### 1. The visit log lives in the enrollment database, not the social one

The natural home looks like door-api's social SQLite, beside check-ins. **That would be wrong**, for a reason worth stating because it is easy to miss:

Retention is *unbounded* (owner's decision). A forever-retained, `person_id`-keyed presence log in the social DB would **survive unenrollment** — ADR-0009 §3 purges the enrollment DB, and nothing would touch the visit rows. Someone who revoked consent and had their face templates destroyed would leave behind a permanent record of every time they came to the door. That breaks ADR-0005 §6 ("all voluntary data is deletable") in the least visible way possible: silently, months later.

So the log lives in `enrollment.sqlite` with a foreign key to `person`:

```sql
CREATE TABLE visit (
    visit_id     TEXT PRIMARY KEY,   -- 'vst_' + base62
    person_id    TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    arrived_at   TEXT NOT NULL,      -- UTC ISO-8601
    last_seen_at TEXT NOT NULL       -- extended while the person stays visible
);
```

Three properties follow for free, which is the point of putting it here:

- **`ON DELETE CASCADE`** — unenrolling destroys the visit history in the same transaction as the embeddings. Deletion stays a single, already-tested code path (ADR-0009 E-5, P-5).
- **It is on the LUKS volume** (ADR-0009 §6 option C). Unbounded presence history is exactly the data a stolen Pi should not yield, and this is the only volume that protects it.
- **`secure_delete=ON`** applies, so purged visit rows are zeroed like embedding rows.

The cost is that door-visiond owns the log and door-api/door-ui read it over the admin API rather than joining locally. That is the correct ownership anyway: the service that knows identities owns identity-linked data.

No `rang` flag. door-visiond cannot know a bell was pressed without door-api calling into it on the ring path, and adding a cross-service call there to populate a nice-to-have column is a poor trade against the critical-path rule. A column that is permanently zero is worse than no column; "did this visit include a ring" is derivable by correlating events in the NUC archive if it is ever wanted.

**Visits, not sightings.** One row per visit, not per frame or per greeting: a stable identity extends `last_seen_at` on an existing open visit if the last sighting was within `VISIT_MERGE_WINDOW_S` (default 600 s), otherwise it opens a new one. Someone pacing in the hallway produces one visit, not forty.

### 2. Attribution binds the session's recognised person

When a session has a recognised visitor, writes made during it carry `person_id`: guestbook entries, poll votes, check-ins. Mechanically this is the identity the session machine already holds — no new inference, no matching at write time.

**Attribution is disclosed, never silent.** The interface says *"voting as Tiger"* / *"posting as Tiger"* before the write. Someone who did not realise the door knew them must not discover it from a stats page afterwards.

**Votes lose ballot secrecy, and that is the owner's informed choice.** Attributed votes are attributable in admin views and stats. Recorded here so it is a decision on the record rather than an accident of implementation.

### 3. Public surfaces: names yes, rankings no

ARCHITECTURE.md §2 classes the wallboard as **Low** trust — a hallway screen anyone can read. ADR-0005 §5 already forbids full visitor logs there, and that stands: **the visit log is admin-only.**

Beyond that the owner wants names shown, so:

| Surface | Shows |
|---|---|
| Wallboard greeting | Name. Transient, and the entire point of the feature. |
| Wallboard guestbook / interactions | Attributed name, **gated by `DOOR_UI_PUBLIC_ATTRIBUTION`** (default on) |
| Wallboard frequency ranking | **Nothing — disabled** (`SOCIAL_FREQUENCY_STAT_ENABLED`, default off) |
| Admin | Everything: visit log, per-person attribution, vote choices |
| Telegram | Name in the ring notification |

Two flags rather than hardcoded behaviour, because both of these are matters of household taste that will change: the owner asked for names but explicitly excluded "who was there the most" *for now*. A flag makes that reversible without a code change, and makes the privacy posture inspectable in one place.

### 4. The name reaches the notification through the session event

`session.state_changed` gains `display_name: str | None`. The NUC evaluates notification rules from that event and has no other route to the name.

This widens where names travel: onto MQTT, into the NUC archive, and out to Telegram. That is authorised here, and bounded — `display_name` only, never `person_id` alongside it in the same public-facing message, and `None` for unrecognised visitors, which keeps the generic "Someone's at the door." path intact. ADR-0009 E-4's firewall is unaffected: it forbids vector-carrying fields, and this is a string that already travels on `vision.identity_stable`.

## Enforcement mechanisms

- **E-21 Visit rows die with the person.** The `visit` table is in `enrollment.sqlite` with `ON DELETE CASCADE`. A visit log anywhere else — the social DB, the NUC archive as the primary copy, a flat file — is a review-blocking defect, because nothing else inherits the deletion guarantee.
- **E-22 Visits are merged, not accumulated.** Sightings within the merge window extend one visit. No code path may write one row per frame, per greeting, or per event.
- **E-23 Attribution is disclosed.** Any surface that attaches identity to a write says so before the write happens. Silent attribution is a review-blocking defect.
- **E-24 The visit log never reaches a public route.** Admin-authenticated only, like `/people`. Public surfaces get the greeting and (flag-gated) attributed names — never arrival history, never counts.
- **E-25 Unrecognised means unchanged.** With no recognised visitor, every behaviour here is exactly as before: generic greeting, no visit row, no attribution, generic notification. Recognition adds; it never gates. Personalisation, never authorisation (ADR-0005 §3) remains untouched — nothing here reaches an access decision.

## Test specification (binding for T-308)

| ID | Test | Where |
|---|---|---|
| P-26 | `test_unenroll_purges_visit_history` — enrol, record visits, unenroll; visit rows gone, byte-scan `enrollment.sqlite` + WAL for the person id → absent, tombstone still present. Extends P-5. | door-visiond |
| P-27 | `test_visits_are_merged_within_the_window` — a person seen repeatedly inside the window yields one visit with an extended `last_seen_at`; a sighting after it opens a second. | door-visiond |
| P-28 | `test_visit_log_requires_admin_auth` — the visit routes reject unauthenticated reads, like `/people`. | door-visiond |
| P-29 | `test_attribution_binds_the_session_identity` — a guestbook entry, vote, and check-in written during a recognised session carry that `person_id`; the same writes in an unrecognised session carry none. | door-api |
| P-30 | `test_notification_names_a_recognised_visitor` — a ring with a recognised visitor produces "(name) is here"; an unrecognised ring produces the generic text unchanged. | control-plane-api |
| P-31 | `test_public_surfaces_never_show_arrival_history` — no public door-api or door-ui route exposes visits or counts, with the attribution flag both on and off. | door-api/door-ui |
| P-32 | `test_frequency_ranking_is_off_by_default` — the frequency stat is absent from public payloads unless explicitly enabled. | door-api |

## Consequences

- Consent goes to **v3**; enrollment tests already read the version from the file, so the bump does not churn them.
- `packages/contracts` changes (`SessionStateChangedPayload.display_name`) — regenerate schemas and TS.
- door-visiond gains visit-log storage and admin routes; its enrollment schema gains one table.
- The wallboard's existing most-frequent-visitor tile goes dark by default. It was fed by voluntary check-ins and is not deleted, just flag-gated.
- Unbounded retention is an owner decision made with the stolen-Pi consequence stated. It is revisitable by adding a prune job; the schema needs no change for that.
