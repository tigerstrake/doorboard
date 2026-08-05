/**
 * PUT — the Pi publishes its sealing public key + the canonical consent text.
 * GET — a phone fetches them so it can seal (and render consent verbatim, E-7).
 *
 * Only the *public* half is ever here. The relay holds no private key and cannot
 * open anything it stores (ADR-0016 E-9).
 */
import { isDeviceRequest, jsonError, jsonOk } from "@/lib/device";
import { getDoorKey, putDoorKey, storageConfigured } from "@/lib/store";
import { InvalidBody, parseDoorKeyPublication } from "@/lib/validate";

export const dynamic = "force-dynamic";

export async function PUT(request: Request): Promise<Response> {
  if (!isDeviceRequest(request)) return jsonError(401, "device_auth_required");
  if (!storageConfigured()) return jsonError(503, "storage_not_configured");

  let publication;
  try {
    publication = parseDoorKeyPublication(await request.json());
  } catch (error) {
    if (error instanceof InvalidBody) return jsonError(422, "invalid_publication");
    return jsonError(400, "malformed_json");
  }

  await putDoorKey(publication);
  return jsonOk({ door_key_id: publication.door_key_id, published: true });
}

export async function GET(): Promise<Response> {
  if (!storageConfigured()) return jsonError(503, "storage_not_configured");
  const record = await getDoorKey();
  if (!record) {
    // The door has not checked in yet — it may be offline or the relay may be
    // freshly deployed. Either way there is nothing to seal to.
    return jsonError(503, "door_key_unavailable");
  }
  return jsonOk(record);
}
