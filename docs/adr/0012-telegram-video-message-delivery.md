# ADR-0012: Telegram delivery of saved visitor video messages

**Status:** Accepted · **Date:** 2026-07-18

Implements [T-609](../tasks/T-609-telegram-video-delivery.md). First outbound
delivery of visitor *media* (not just text) to a third-party cloud, so it needs
a recorded decision on placement, trigger, and privacy.

## Context

A visitor can leave a video message at the door. Today the clip lands on the
Pi SSD and is archived to the NAS; the only "message to a person" mechanism is
the ntfy text notifier on the NUC, and no rule even fires when a video message
is saved. Residents have no way to actually receive the video.

The owner wants the saved clip pushed to Telegram (chosen over WhatsApp: the
official WhatsApp Business API needs a Meta app + approved templates, and the
unofficial gateways violate WhatsApp's ToS; Telegram's Bot API is official,
free, needs only a bot token + chat id, and accepts video uploads to 50 MB).

Binding constraints:

- **Trust model** (`../../ARCHITECTURE.md` §2): the door Pi is physically
  stealable and holds no admin credentials; the NUC is the only tier that may
  hold outbound integration secrets. A Telegram bot token is such a secret.
- **Critical path** (ADR-0002 / `../../ARCHITECTURE.md` §1): the door
  interaction path must never block on the NUC, NAS, or cloud.
- **Privacy** (`../../ARCHITECTURE.md` §9, [ADR-0009](0009-enrollment-and-biometric-data.md)):
  visitor media is visitor-initiated, minimally retained, and deletable.
- **Storage** ([ADR-0007](0007-storage-tiers.md)): the durable clip lives on
  the SSD/NAS, not the NUC.

## Decision

1. **Placement — the NUC (`control-plane-api`).** The bot token and door-api
   admin token live only on the NUC, alongside the existing ntfy notifier.
   The door Pi never talks to Telegram.
2. **Trigger — the session transition to `VIDEO_MESSAGE_SAVED`**, consumed off
   the existing ingest fan-out (`service.ingest_batch`), never from a bell
   press. Firing on *saved* (not `VIDEO_MESSAGE_REVIEW`) guarantees a message
   the visitor discards or lets time out is never sent — a privacy invariant.
   The `session_id → recording_id` link is resolved from the already-projected
   `media_mirror` table (kind `video_message`, not soft-deleted).
3. **File access — pull on demand, persist nothing new.** The NUC fetches the
   clip bytes from door-api's existing admin endpoint
   (`GET /admin/media-inbox/{id}/file`, bearer-token) and streams them straight
   to Telegram. No second copy of visitor media is stored on the NUC, so
   deletion/retention on the SSD/NAS remains the single source of truth. (The
   NAS was the alternative source; door-api reuse avoids a NAS mount + its
   protocol/credentials and needs only config the operator already has.)
4. **Fail-safe & off-path.** Delivery is best-effort: disabled (silent no-op)
   unless bot token + chat id(s) + door-api creds are all set, and every
   transport error is swallowed with a warning, exactly like the ntfy channel.
   Clips over `TELEGRAM_MAX_VIDEO_BYTES` (50 MB) send a text pointer instead.

## Consequences

- Residents receive the actual video on Telegram seconds after it's saved; the
  operator only supplies credentials. Multiple `TELEGRAM_CHAT_IDS` are
  supported (you / roommate / a shared group) — a pragmatic stand-in for a full
  per-recipient routing model, which remains future work.
- New, deliberate trust edge: the NUC calls back to door-api's admin API to
  read media. This is read-only, off the critical path, and uses the NUC's
  legitimately-held admin token; the Pi still holds no NUC secret.
- **Privacy note:** enabling this sends visitor-recorded video to Telegram
  (Meta-adjacent third party). It is opt-in, fires only on visitor-*saved*
  clips, and creates no new local copy, but operators must treat the configured
  chats as a disclosure boundary. Deletion on the SSD/NAS does not recall an
  already-sent Telegram message.
- No `packages/contracts` change: the feature consumes existing events only.
