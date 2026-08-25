/**
 * GET — a visitor reads their own session snapshot.
 *
 * The token in the path is hashed and matched against what door-api pushed; the
 * relay holds no HMAC secret and cannot mint or verify a token itself (E-16). An
 * unknown token and an expired session are indistinguishable in the response, so
 * this cannot be used to probe whether a session exists.
 *
 * The token hash is stripped before responding: it authorises the request and has
 * no business travelling back into browser history or a screenshot.
 */
import { RATE_LIMITS, clientAddress, jsonError, jsonOk, sha256Base64Url } from "@/lib/device";
import type { RelayStore } from "@/lib/relayTypes";

export async function handleGet(
  request: Request,
  store: RelayStore,
  params: Record<string, string>,
): Promise<Response> {
  if (!store.configured()) return jsonError(503, "storage_not_configured");

  const limit = RATE_LIMITS.visitorRead;
  if (!(await store.underRateLimit("vread", clientAddress(request), limit.limit, limit.windowS))) {
    return jsonError(429, "rate_limited");
  }

  const token = params.token!;
  const snapshot = await store.getVisitorSnapshotByTokenHash(sha256Base64Url(token));
  if (!snapshot) return jsonError(404, "session_not_found");

  // Named field by field rather than spread-minus-one: a future field added to the
  // stored snapshot must be an explicit decision to publish, not a default.
  return jsonOk({
    session_id: snapshot.session_id,
    state: snapshot.state,
    expires_at: snapshot.expires_at,
    poll: snapshot.poll ?? null,
    poll_results: snapshot.poll_results ?? null,
    outcomes: snapshot.outcomes ?? [],
    // A boolean, never a name (ADR-0044): disclose that a write will be attributed
    // without handing a resident's name to whoever holds the QR token.
    attributed: snapshot.attributed === true,
    pushed_at: snapshot.pushed_at,
  });
}
