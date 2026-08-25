/**
 * PUT — the Pi registers an invite it just minted (ADR-0016 §4 step 1).
 *
 * Only `sha256(secret)` arrives, never the secret, so this store cannot be used
 * to reconstruct a working invite URL. And because the Pi independently
 * re-verifies every invite against its own table before enrolling (step 2), a
 * forged record here authorizes nothing (E-11).
 */
import { isDeviceRequest, jsonError, jsonOk } from "@/lib/device";
import { resolveStore } from "@/lib/relayStore";
import type { RelayStore } from "@/lib/relayTypes";
import { InvalidBody, parseInviteRegistration } from "@/lib/validate";

export const dynamic = "force-dynamic";

export async function PUT(request: Request, store: RelayStore = resolveStore()): Promise<Response> {
  if (!isDeviceRequest(request)) return jsonError(401, "device_auth_required");
  if (!store.configured()) return jsonError(503, "storage_not_configured");

  let registration;
  try {
    registration = parseInviteRegistration(await request.json());
  } catch (error) {
    if (error instanceof InvalidBody) return jsonError(422, "invalid_registration");
    return jsonError(400, "malformed_json");
  }

  await store.putInvite(registration.invite_id, {
    secret_sha256: registration.secret_sha256,
    expires_at: registration.expires_at,
    max_images: registration.max_images,
  });
  return jsonOk({ invite_id: registration.invite_id, registered: true });
}
