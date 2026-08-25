/**
 * GET — progress for the phone that submitted this bundle.
 *
 * Carries a state and a machine-readable reason, both bounded, and never a name
 * or anything derived from the photos (ADR-0016 §5). Status intentionally outlives
 * the ciphertext so an enrollee can still be told what happened.
 */
import { RATE_LIMITS, clientAddress, jsonError, jsonOk } from "@/lib/device";
import type { RelayStore } from "@/lib/relayTypes";

export async function handleGet(
  request: Request,
  store: RelayStore,
  params: Record<string, string>,
): Promise<Response> {
  if (!store.configured()) return jsonError(503, "storage_not_configured");

  const limit = RATE_LIMITS.status;
  if (!(await store.underRateLimit("status", clientAddress(request), limit.limit, limit.windowS))) {
    return jsonError(429, "rate_limited");
  }

  const bundleId = params.bundleId!;
  const record = await store.getStatus(bundleId);
  if (!record) {
    // Either never submitted, or the status TTL lapsed an hour after the fact.
    return jsonOk({ bundle_id: bundleId, status: "expired", reason: null, updated_at: new Date().toISOString() });
  }
  return jsonOk({ bundle_id: bundleId, ...record });
}
