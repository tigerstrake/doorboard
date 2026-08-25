/**
 * DELETE — the Pi revokes an invite it no longer honours.
 *
 * Advisory only: the Pi has already refused the invite locally, so this exists to
 * stop the relay accepting pointless uploads, not to enforce anything.
 */
import { isDeviceRequest, jsonError, jsonOk } from "@/lib/device";
import type { RelayStore } from "@/lib/relayTypes";

export async function handleDelete(
  request: Request,
  store: RelayStore,
  params: Record<string, string>,
): Promise<Response> {
  if (!isDeviceRequest(request)) return jsonError(401, "device_auth_required");
  if (!store.configured()) return jsonError(503, "storage_not_configured");

  const inviteId = params.inviteId!;
  await store.deleteInvite(inviteId);
  return jsonOk({ invite_id: inviteId, revoked: true });
}
