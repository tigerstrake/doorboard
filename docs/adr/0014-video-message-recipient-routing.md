# ADR-0014: Route a saved video message to chosen recipients

**Status:** Accepted · **Date:** 2026-07-20

Extends [ADR-0012](0012-telegram-video-message-delivery.md), whose Consequences
named per-recipient routing as future work: multiple `TELEGRAM_CHAT_IDS` were "a
pragmatic stand-in for a full per-recipient routing model." This ADR and its PR
add the routing *backend* — the config, the contract field, the door-api emit,
and the control-plane routing. The DoorPad review-screen buttons (Tiger / Adam /
both / Discard) that let a visitor pick are a separate follow-up PR.

## Context

When a visitor saves a video message, `session.state_changed` →
`VIDEO_MESSAGE_SAVED` is consumed on the control plane, which pulls the clip
from door-api and sends it to Telegram (ADR-0012). Today it broadcasts to
*every* chat in `TELEGRAM_CHAT_IDS`: a message meant for one resident reaches
the whole household. The owner wants a visitor to be able to direct a message to
a specific resident (Tiger, Adam, or both).

Binding constraints:

- **Trust model & placement** (`../../ARCHITECTURE.md` §2, ADR-0012): outbound
  integration secrets and the key→chat-id mapping live only on the NUC. The
  door Pi never learns anyone's Telegram chat id; it only carries opaque
  recipient *keys*.
- **Contract discipline** (ADR-0003, `docs/protocols/events.md`): carrying the
  choice needs a new field on an existing event, an ADR, and regenerated
  schema/TS/fixtures.
- **Privacy** (`../../ARCHITECTURE.md` §9, [ADR-0005](0005-privacy-model.md),
  ADR-0012): a saved clip is already a deliberate, visitor-initiated disclosure;
  narrowing *who* receives it only tightens that boundary. Personalization is
  never authorization.

## Decision

1. **Recipient keys, resolved only on the NUC.** A recipient is identified by a
   short lowercase key (a resident id such as `tiger` / `adam`). The NUC holds
   the mapping in a new setting `VIDEO_MESSAGE_RECIPIENTS`, formatted
   `key:chatid` comma-separated — e.g. `"tiger:8397445760,adam:"`. It parses to
   `{key: chat_id}`. A **blank/missing chat id** (`"adam:"`) means a *known
   recipient with no chat configured yet*: a legitimate, expected state (Adam's
   id is filled in later), not an error.

2. **Optional contract field carries the choice.** Add
   `recipients: list[str] | None = None` to `SessionStateChangedPayload` — the
   chosen recipient KEYS. It is only populated on the `VIDEO_MESSAGE_SAVED`
   transition. `None`/absent (the default) means legacy broadcast, so existing
   producers and consumers stay backward-compatible; the regenerated JSON schema
   leaves `recipients` out of `required` and the TS type marks it optional.

3. **door-api emits keys, never chat ids.** The save handler/endpoint accepts an
   optional list of keys (the UI PR will pass them; default `None`), validates
   their shape (short strings, de-duplicated, capped), and threads them onto the
   emitted event. The Pi resolves nothing and stores no chat id.

4. **Control-plane routing, with a broadcast fallback.** On
   `VIDEO_MESSAGE_SAVED`, the delivery resolves each key through
   `VIDEO_MESSAGE_RECIPIENTS` and sends the clip **only** to the matched chat
   ids. `recipients` `None`/empty preserves the ADR-0012 behavior: broadcast to
   all `TELEGRAM_CHAT_IDS`. Owner notifications (missed bell, storage, etc.)
   still go to all chats — routing narrows video delivery only.

5. **Unconfigured ⇒ saved, not sent (consent/privacy stance).** A chosen
   recipient whose chat id is blank, or an unknown key, is logged
   (`telegram_video_recipient_unconfigured` / `_unknown`) and skipped. If *no*
   chosen recipient resolves to a chat id, the clip stays saved on door-api and
   nothing is sent — no error, no accidental broadcast. Directing a message to a
   resident is never a fallback into disclosing it to everyone; the safe default
   of an unresolved route is silence, not fan-out.

## Consequences

- A visitor can direct a message to a specific resident; delivery targets only
  that resident's Telegram chat. Broadcast remains the default for un-routed
  (legacy) messages, so nothing regresses before the UI ships.
- `packages/contracts` gains an additive, backward-compatible field
  (schema/TS/fixtures regenerated). Consumers that ignore `recipients` behave
  exactly as before.
- New config `VIDEO_MESSAGE_RECIPIENTS` on the NUC only. Adam's key ships blank
  (`adam:`), so a message routed to Adam today is saved-not-sent until his chat
  id is filled in — a deliberate, logged no-op rather than a failure.
- No new trust edge or media copy: the Pi still holds no chat ids, the clip is
  still pulled on demand and never re-stored on the NUC (ADR-0007, ADR-0012),
  and deletion on the SSD/NAS stays the single source of truth.
- **Deferred:** the DoorPad review-screen buttons that let a visitor choose a
  recipient are a follow-up PR; until then callers may pass keys directly to the
  save endpoint, or omit them for a broadcast.
