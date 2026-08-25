/**
 * POST — door-api reports what it did with collected visitor actions.
 *
 * Each outcome retires its action and is folded into the visitor's snapshot, so the
 * phone's next read shows "note saved" or a reason it was refused. Reasons are
 * bounded and machine-readable; they are not a channel for echoing content back.
 */
import { isDeviceRequest, jsonError, jsonOk } from "@/lib/device";
import type { RelayStore } from "@/lib/relayTypes";
import { InvalidBody, parseVisitorAck } from "@/lib/validate";

export async function handlePost(request: Request, store: RelayStore): Promise<Response> {
  if (!isDeviceRequest(request, "visitor")) return jsonError(401, "device_auth_required");
  if (!store.configured()) return jsonError(503, "storage_not_configured");

  let outcomes;
  try {
    outcomes = parseVisitorAck(await request.json());
  } catch (error) {
    if (error instanceof InvalidBody) return jsonError(422, "invalid_ack");
    return jsonError(400, "malformed_json");
  }

  for (const outcome of outcomes) {
    await store.completeVisitorAction(outcome.session_id, outcome.action_id, {
      action_id: outcome.action_id,
      kind: outcome.kind,
      status: outcome.status,
      reason: outcome.reason,
      entry_id: outcome.entry_id,
    });
  }

  return jsonOk({ acknowledged: outcomes.length });
}
