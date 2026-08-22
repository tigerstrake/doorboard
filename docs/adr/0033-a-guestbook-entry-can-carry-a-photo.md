# ADR-0033: a guestbook entry can carry a photo

**Status:** Accepted · **Date:** 2026-08-21

## Context

Check-ins can already reference a visitor-captured photo
(`SocialCheckinCreatedPayload.photo_recording_id`, ADR-0013). Guestbook entries cannot, so a
visitor who wants to leave a note *and* a picture has to do two separate things, and the note
and the photo end up unlinked.

The owner asked for the two to behave differently on purpose: a photo is offered with **every**
check-in, but is **optional** on a guestbook entry. Both use the same "capture, show, then
confirm" step — nothing is recorded until the visitor agrees — so recognition still only
"changes a greeting and a colour", which is what the About screen tells visitors.

## Decision

`SocialGuestbookEntryCreatedPayload` gains `photo_recording_id: str | None = None`, mirroring
the check-in payload exactly.

The field holds only a **reference**. The image itself stays in the photo-booth pipeline, private
under `photo-review/` until the owner approves it, so attaching an id never bypasses review or
consent, and never puts image data on a public route. Declining, skipping, or walking away
mid-confirm discards the capture.

`guestbook_entries` gains a matching nullable column, added through the store's existing
`_ensure_column` migration.

The control-plane `social_items` mirror does **not** carry the reference, matching what
check-ins already do: `_project_social_checkin_created` has never stored
`photo_recording_id` either, and `SocialItemRow` has no column for it. The mirror exists for
moderation and deletion of *text*; a photo's lifecycle belongs to the media pipeline, where
retention, owner approval and the ADR-0009 person purge already act on it. Adding a Postgres
column to duplicate that would create a second place for the same fact to go stale.

The reference is exposed on the **owner-facing** guestbook shape only, never the public one.
Guestbook entries render on a wallboard facing a shared hallway, and a photo is private until
the owner approves it — publishing the reference would reveal which entries carry a face
before that decision is made.

### Deployment ordering

Per [ADR-0031](0031-additive-contract-fields-are-not-backward-compatible.md), this field is
additive to the schema but **not** backward compatible at runtime: `StrictModel` sets
`extra="forbid"`, so a consumer built before this change *rejects* an event carrying the new key
rather than ignoring it.

door-api is the producer and publishes upward through `/ingest`. Therefore:

**Deploy the NUC (`control-plane-api` + `wallboard-worker`) at or before the door.** The door may
lead the NUC in version, but must not lead it in *this* field — a door emitting
`photo_recording_id` at an older NUC gets the guestbook event rejected at ingest and
dead-lettered in door-sync. That failure is visible (`event rejected by NUC: ... validation
error`) but it costs the entry.

Nothing here is on the door's critical path: the guestbook is not part of
`button → ESP32 feedback → local UI`, and a rejected mirror never blocks the local write
(ADR-0018 E-25 — recognition and now photos add to a note, they never gate it).

## Consequences

- A guestbook entry and its photo are linked, so the end-of-year collage can draw on both.
- One more nullable column and one more optional contract field to carry.
- The ordering constraint above is real and has bitten before: 15 events sat dead-lettered from
  `2026-08-06` for exactly this class of reason.
- Guestbook photos inherit the check-in photo's privacy path unchanged — private until
  approved, owner-deletable, and removed by the ADR-0009 person purge.
