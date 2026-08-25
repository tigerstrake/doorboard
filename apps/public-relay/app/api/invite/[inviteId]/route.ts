/**
 * DELETE — the Pi revokes an invite it no longer honours.
 *
 * Advisory only: the Pi has already refused the invite locally, so this exists to
 * stop the relay accepting pointless uploads, not to enforce anything.
 */
import { isDeviceRequest, jsonError, jsonOk } from "@/lib/device";
import { resolveStore } from "@/lib/relayStore";
import type { RelayStore } from "@/lib/relayTypes";

export const dynamic = "force-dynamic";

export async function DELETE(
  request: Request,
  context: { params: Promise<{ inviteId: string }> },
  store: RelayStore = resolveStore(),
): Promise<Response> {
  if (!isDeviceRequest(request)) return jsonError(401, "device_auth_required");
  if (!store.configured()) return jsonError(503, "storage_not_configured");

  const { inviteId } = await context.params;
  await store.deleteInvite(inviteId);
  return jsonOk({ invite_id: inviteId, revoked: true });
}
