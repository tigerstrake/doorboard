# UI spec — Visitor phone flow (`/visitor`)

Reached only via the QR code shown in wallboard visitor mode. Tokenized: the QR encodes a short-lived signed token scoped to the current session (packages/auth); expired/invalid tokens get a friendly dead-end page. Rate-limited per token and IP.

## Capabilities (deliberately minimal, v1)

- See ring status ("ringing… / answered / no answer").
- Leave a text note if unanswered (same sanitization/rate limits as guestbook).
- Vote in the current poll.
- Read the camera/privacy notice; submit a deletion request.

No live video to the visitor's phone in v1 (owner-side live view is separate, local-network only). No login, no persistent identity, nothing to install — plain mobile web page served by the Pi.
