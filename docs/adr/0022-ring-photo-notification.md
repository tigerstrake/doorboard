# ADR-0022: The ring photo is a second message, not a slower first one

**Status:** Accepted · **Date:** 2026-08-17

Extends [ADR-0012](0012-telegram-video-message-delivery.md) (Telegram delivery from the
NUC). Does not change the door critical path or ARCHITECTURE.md §4's latency budgets.

## Context

The owner asked for a picture of whoever rings, alongside the existing text alert, and was
explicit about the ordering: *"if that takes longer then just send the text first and send
the pic as soon as that is possible."*

That instinct matches the hardware. The text alert fires from `notify.py`'s
`doorbell_rang` rule the moment a session reaches `RINGING`. A *picture*, however, does
not exist at that moment:

- door-media records the bell clip from the already-live stream, then finalizes it,
  then cuts a thumbnail, then emits `media.thumbnail_ready`. That is seconds after the
  press, not milliseconds.
- The NUC learns about any of it only through the ingest fan-out, which is by design not
  in the door path at all.

So "one message with both" would mean holding the ring alert until a clip finalizes —
delaying the notification whose entire value is immediacy.

## Decision

**Two independent messages.** The text stays exactly as it is, on `RINGING`. The picture
is delivered separately, triggered by `media.thumbnail_ready` for a `bell_clip`.

They cannot block each other: no picture ever still means the owner got the text, and a
slow thumbnail is a late second message rather than a late first one.

**The still comes from the bell clip that already exists.** door-media generated
thumbnails on finalize, recorded them in its database and announced them — and had no way
to serve one. It gains `GET /recordings/{id}/thumbnail`; door-api proxies it at
`GET /admin/media-inbox/{id}/thumbnail`.

This is deliberately *not* a new capture. Taking a fresh snapshot on the bell press would
mean a second camera access on the one path that must never grow work, and would create a
frame whose retention nothing governs. The thumbnail is part of a recording that already
exists, is already covered by the bell-clip retention policy, and is already deleted when
that clip is.

**`bell_clip` only.** `photo_booth` and `video_message` thumbnails are ignored. A
photo-booth still is a visitor's own consented artifact with its own flow, and pushing one
to the owner's phone unbidden would be a privacy defect rather than a feature. Video
messages already deliver as video (ADR-0012). The kind is checked *before* the fetch, so
an ignored kind costs no HTTP call.

**Unmirrored means unsent.** If the media mirror has no row for the recording, the door
cannot tell a bell clip from a photo-booth still, so nothing is sent. Guessing is the one
outcome that could leak the wrong frame.

**The proxy is admin-authenticated, and the door-api route accepts any kind.** The
neighbouring `/file` route is scoped to `video_message` because it exists for the DoorPad's
review screen; a bell clip needs the same bytes through a different gate. A thumbnail is a
frame of whoever was at the door, which is exactly what ARCHITECTURE.md §2 keeps off
low-trust surfaces — so it is never public, and the NUC (the legitimate holder of admin
credentials) is what fetches it.

**`RING_PHOTO_ENABLED`, separate from `DOORBELL_NOTIFY_ENABLED`.** Some people want to
know somebody is at the door without a frame of every passing delivery driver arriving on
their phone. Default on, one flag to turn off, and turning it off leaves the text intact.

## Consequences

- Two Telegram messages per ring instead of one. Accepted, and the caption stands alone
  because it may land well after the text.
- The NUC now pulls an image from the Pi on every ring. Bounded by the thumbnail's size
  and off the door path, in the same fan-out that already pulls video-message clips.
- Its fan-out has its own `try/except`, so a Telegram failure cannot cost the video
  delivery beside it or the ingest result.
- A frame of a visitor now reaches the owner's phone, where before it reached only the
  wallboard and the recording. This is the owner's doorbell and the clip was already being
  recorded, so the change is in convenience rather than in what the door captures — but it
  is a change in where visitor imagery travels, and belongs on the record for that reason.
- No new capture path, no new retention rule, and nothing added to the bell press.
