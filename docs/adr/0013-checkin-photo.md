# ADR-0013: A check-in may reference an optional visitor photo

**Status:** Accepted · **Date:** 2026-07-20

Foundation for the "photo check-in" feature (a year-end visitor collage). This
ADR and its PR change only the contract and the door-api backend; the DoorPad
capture UX, the gallery/collage surfaces, and any stats come in later PRs.

## Context

A visitor can voluntarily check in at the door (`POST /checkins`,
[T-403](../tasks/T-403-social-crud.md)); the check-in optionally carries a
freeform `label` and a server-derived `person_id`, and emits
`social.checkin_created`. It has no way to remember *what the visitor looked
like* at that moment.

Separately, the photo-booth flow ([T-606](../tasks/T-606-photobooth-gallery.md))
already lets a visitor deliberately capture a `photo_booth`-kind recording, with
explicit consent metadata (`capture_mode: explicit_photo_booth`), stored on the
SSD and archived like any other media (ADR-0007). The owner wants a year-end
collage built from these check-in photos — which needs a durable link between a
check-in and the photo the visitor chose to take.

Binding constraints:

- **Privacy** (`../../ARCHITECTURE.md` §9, [ADR-0009](0009-enrollment-and-biometric-data.md)):
  visitor media is visitor-initiated, minimally retained, and deletable; a face
  seen at the door is never persisted without opt-in. A collage is a *public*
  use of a visitor's likeness and must not happen silently.
- **Contract discipline** (ADR-0003, `docs/protocols/events.md`): the link is a
  new field on an existing event and needs an ADR + a regenerated schema.
- **Single source of truth for media** (ADR-0007, [ADR-0012](0012-telegram-video-message-delivery.md)):
  the photo bytes live in the photo-booth/gallery pipeline; nothing else copies
  or re-stores them.

## Decision

1. **A check-in MAY reference one photo, by id only.** Add an optional
   `photo_recording_id: str | None = None` to `SocialCheckinCreatedPayload`, to
   the `CheckinCreateRequest`, to the `checkins` table, and to the emitted
   event. It points at an existing `photo_booth`-kind recording; the check-in
   stores *only the reference*, never image bytes. Absent/`null` (the default)
   is a normal, anonymous check-in — unchanged behavior.

2. **Consent-first, in stages.** The reference only exists because the visitor
   opted in twice: first to capture the photo (the photo-booth flow's own
   consent), then to attach it to their check-in. On capture the photo is
   **private** — held in the SSD/gallery pipeline, not shown publicly. Any
   *public or collage* use requires a later, separate **owner approval** step
   (a subsequent PR); a stored `photo_recording_id` is never itself
   authorization to publish. Personalization is never authorization
   (`../../ARCHITECTURE.md` §9).

3. **`person_id` stays server-derived.** The client may set `photo_recording_id`
   but still never `person_id`; check-in attribution remains derived only from
   the session's cached identity. A photo reference does not attribute a
   check-in to anyone.

4. **Deletion stays intact and unified.** The photo is deletable through the
   existing `social.deletion_requested` path (`target_kind: "photo"`, owned by
   door-media/control-plane); deleting the check-in (`target_kind: "checkin"`)
   drops the reference. Deleting the referenced photo leaves a dangling
   `photo_recording_id`, which consumers (gallery, future collage) must treat as
   "photo gone" — the reference is a pointer, not a copy.

## Consequences

- `packages/contracts` gains an additive, backward-compatible field; the
  regenerated JSON schema leaves `photo_recording_id` out of `required` and TS
  types mark it optional, so existing producers and consumers (e.g. the
  control-plane social projection) keep working unchanged.
- door-api can now persist and emit the link; the DoorPad capture/attach UX, the
  owner-approval gate, and the collage/gallery rendering are explicitly deferred
  to later PRs and must honor the consent staging above.
- No new media storage or trust edge: the photo bytes remain solely in the
  photo-booth/gallery pipeline (ADR-0007); door-api holds only an id.
