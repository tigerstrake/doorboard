/**
 * PUT — door-api pushes the current visitor session snapshot (ADR-0017 §2).
 *
 * Visitor scope only. A door-visiond token is rejected here, and this token is
 * rejected on every enrollment route (E-17), so a compromise of one Pi service
 * does not hand over the other's surface.
 */
import { isDeviceRequest, jsonError, jsonOk } from "@/lib/device";
import { resolveStore } from "@/lib/relayStore";
import type { RelayStore } from "@/lib/relayTypes";
import { InvalidBody, parseVisitorSnapshot } from "@/lib/validate";

export const dynamic = "force-dynamic";

export async function PUT(request: Request, store: RelayStore = resolveStore()): Promise<Response> {
  if (!isDeviceRequest(request, "visitor")) return jsonError(401, "device_auth_required");
  if (!store.configured()) return jsonError(503, "storage_not_configured");

  let snapshot;
  try {
    snapshot = parseVisitorSnapshot(await request.json());
  } catch (error) {
    if (error instanceof InvalidBody) return jsonError(422, "invalid_snapshot");
    return jsonError(400, "malformed_json");
  }

  await store.putVisitorSnapshot(snapshot);
  return jsonOk({ session_id: snapshot.session_id, pushed: true });
}
