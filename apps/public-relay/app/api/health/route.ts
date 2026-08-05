/**
 * GET /api/health — reachability and configuration, nothing more.
 *
 * Deliberately says nothing about who has enrolled or which invites exist: this
 * is a public route on an untrusted host (ARCHITECTURE.md §2).
 */
import { deviceTokenConfigured, jsonOk } from "@/lib/device";
import { getDoorKey, pendingCount, storageConfigured } from "@/lib/store";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  const configured = storageConfigured() && deviceTokenConfigured();
  if (!configured) {
    return jsonOk({
      service: "public-relay",
      status: "degraded",
      storage_configured: storageConfigured(),
      device_token_configured: deviceTokenConfigured(),
      door_checked_in: false,
      pending_bundles: 0,
    });
  }

  let doorCheckedIn = false;
  let pending = 0;
  let status = "ok";
  try {
    doorCheckedIn = (await getDoorKey()) !== null;
    pending = await pendingCount();
  } catch {
    status = "degraded";
  }

  return jsonOk({
    service: "public-relay",
    status,
    storage_configured: true,
    device_token_configured: true,
    door_checked_in: doorCheckedIn,
    pending_bundles: pending,
  });
}
