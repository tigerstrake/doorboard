# T-609: Telegram delivery of saved visitor video messages

**Agent:** claude · **Milestone:** M7 · **Depends on:** T-402, T-501, T-502

Privacy/secret-handling feature (visitor media leaving the device to a cloud),
so it sits in the Claude tier. Decision recorded in
[ADR-0012](../adr/0012-telegram-video-message-delivery.md).

## Context

A visitor can leave a video message (T-402), the clip is recorded on the Pi and
archived by door-sync (T-502), and the NUC already owns owner notifications
(T-501, ntfy). But nothing fires when a video message is *saved*, and there is
no way for a resident to actually receive the clip — they must open the admin
inbox manually. The owner wants the saved video pushed to Telegram (an official,
free Bot API — unlike WhatsApp, which needs Meta approval or a ToS-violating
gateway). Placement, trigger, and privacy constraints are set by the trust model
and [ADR-0012](../adr/0012-telegram-video-message-delivery.md).

## Deliverables

- `control_plane_api.telegram`: a `TelegramClient` (Bot API `sendVideo` /
  `sendMessage`), a `DoorApiVideoSource` (pulls the clip from door-api's admin
  media endpoint), and a `VideoMessageDelivery` that fires on the session
  transition to `VIDEO_MESSAGE_SAVED`, correlates `session_id → recording_id`
  via the `media_mirror` projection, and sends the clip to the configured chats.
- Wiring into the ingest fan-out (`service.ingest_batch`) as a best-effort step,
  parallel to MQTT and ntfy, that can never block or fail ingestion.
- Settings + `.env.example`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_IDS`
  (comma-separated), `TELEGRAM_API_BASE_URL`, `TELEGRAM_MAX_VIDEO_BYTES`,
  `CONTROL_PLANE_DOOR_API_BASE_URL`, `CONTROL_PLANE_DOOR_API_ADMIN_TOKEN`.
- Unit tests for the trigger/lookup logic, the transport, and the fail-safes.
- [ADR-0012](../adr/0012-telegram-video-message-delivery.md).

## Out of scope

- Per-recipient routing rules (which resident gets which message). Multiple
  chat ids are supported as a stand-in; a real recipient model is future work.
- WhatsApp / other channels; changing the ntfy alert rules.
- A new media-upload path to the NUC or NAS (the clip is pulled from door-api).
- Any change to `packages/contracts` (existing events are sufficient).
- Any door-Pi-side change; the door critical path is untouched.

## Acceptance criteria

- Saving a video message delivers the clip to every configured Telegram chat;
  a discarded/timed-out message (never reaching `VIDEO_MESSAGE_SAVED`) is never
  sent, and neither are bell clips or soft-deleted recordings.
- Feature is a silent no-op unless bot token + chat id(s) + door-api creds are
  all configured; no transport error can affect ingestion.
- Clips larger than the configured cap send a text pointer instead of failing.
- No secret lives on the door Pi; the NUC holds the bot and door-api tokens.
- `scripts/lint`, `scripts/typecheck`, and `scripts/test` pass.
