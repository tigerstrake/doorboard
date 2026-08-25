/**
 * POST — a visitor submits a note, a poll vote, or a deletion request.
 *
 * The relay queues it and stops there. It applies size caps and rate limits, then
 * passes the text through untouched: sanitisation, moderation, and the social rate
 * limits stay door-api's existing code, so this cannot become a second, weaker
 * validator that drifts from the LAN path (E-18).
 *
 * These writes are plaintext, and that is the deliberate, documented position —
 * a guestbook note exists to be shown on a hallway wallboard, so encrypting it
 * end-to-end would be theatre (ADR-0017).
 */
import { RATE_LIMITS, clientAddress, jsonError, jsonOk, sha256Base64Url } from "@/lib/device";
import type { RelayStore } from "@/lib/relayTypes";
import { InvalidBody, newActionId, parseVisitorWrite } from "@/lib/validate";

/** Session states in which a visitor may still write. */
const WRITABLE_STATES = new Set([
  "VISITOR_MODE",
  "RINGING",
  "ANSWERED",
  "UNANSWERED_TIMEOUT",
  "VIDEO_MESSAGE_OFFERED",
  "VIDEO_MESSAGE_RECORDING",
  "VIDEO_MESSAGE_REVIEW",
  "VIDEO_MESSAGE_SAVED",
]);

export async function handlePost(
  request: Request,
  store: RelayStore,
  params: Record<string, string>,
): Promise<Response> {
  if (!store.configured()) return jsonError(503, "storage_not_configured");

  const token = params.token!;
  const perIp = RATE_LIMITS.visitorWritePerIp;
  if (!(await store.underRateLimit("vwrite-ip", clientAddress(request), perIp.limit, perIp.windowS))) {
    return jsonError(429, "rate_limited");
  }

  const snapshot = await store.getVisitorSnapshotByTokenHash(sha256Base64Url(token));
  if (!snapshot) return jsonError(404, "session_not_found");

  const perSession = RATE_LIMITS.visitorWritePerSession;
  if (
    !(await store.underRateLimit(
      "vwrite-sess",
      snapshot.session_id,
      perSession.limit,
      perSession.windowS,
    ))
  ) {
    return jsonError(429, "rate_limited");
  }

  if (Date.parse(snapshot.expires_at) <= Date.now()) return jsonError(410, "session_expired");
  if (!WRITABLE_STATES.has(snapshot.state)) return jsonError(409, "session_not_writable");

  let write;
  try {
    write = parseVisitorWrite(await request.json());
  } catch (error) {
    if (error instanceof InvalidBody) return jsonError(422, "invalid_action");
    return jsonError(400, "malformed_json");
  }

  const actionId = newActionId();
  await store.queueVisitorAction(actionId, {
    action_id: actionId,
    session_id: snapshot.session_id,
    submitted_at: new Date().toISOString(),
    note: write.kind === "note" ? write : null,
    vote: write.kind === "vote" ? write : null,
    deletion_request: write.kind === "deletion_request" ? write : null,
  });

  return jsonOk({ action_id: actionId, status: "queued" }, 202);
}
