/**
 * POST — the Pi reports what became of a bundle.
 *
 * This is where ciphertext is deleted (ADR-0016 §5). An `enrolled` outcome also
 * marks the invite consumed so the relay stops accepting uploads against it —
 * belt to the Pi's braces, which already consumed it transactionally (E-11).
 */
import { isDeviceRequest, jsonError, jsonOk } from "@/lib/device";
import { finishBundle, markInviteConsumed, storageConfigured } from "@/lib/store";
import { InvalidBody, parsePickupAck } from "@/lib/validate";
import type { BundleState } from "@/lib/store";

export const dynamic = "force-dynamic";

const OUTCOME_TO_STATE: Record<string, BundleState> = {
  enrolled: "enrolled",
  failed: "failed",
  // A rejection is terminal for this bundle too: the enrollee needs a new invite.
  rejected: "failed",
};

export async function POST(request: Request): Promise<Response> {
  if (!isDeviceRequest(request)) return jsonError(401, "device_auth_required");
  if (!storageConfigured()) return jsonError(503, "storage_not_configured");

  let ack;
  try {
    ack = parsePickupAck(await request.json());
  } catch (error) {
    if (error instanceof InvalidBody) return jsonError(422, "invalid_ack");
    return jsonError(400, "malformed_json");
  }

  const state = OUTCOME_TO_STATE[ack.outcome] ?? "failed";
  const inviteId = await finishBundle(ack.bundle_id, state, ack.reason ?? null);

  if (ack.outcome === "enrolled" && inviteId) await markInviteConsumed(inviteId);

  return jsonOk({ bundle_id: ack.bundle_id, acknowledged: true });
}
