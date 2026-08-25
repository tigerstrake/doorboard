/**
 * GET /api/health — reachability and configuration, nothing more.
 *
 * Deliberately says nothing about who has enrolled or which invites exist: this
 * is a public route on an untrusted host (ARCHITECTURE.md §2).
 */
import { deviceTokenConfigured, jsonOk } from "@/lib/device";
import { resolveStore } from "@/lib/relayStore";
import type { RelayStore } from "@/lib/relayTypes";

export const dynamic = "force-dynamic";

export async function GET(store: RelayStore = resolveStore()): Promise<Response> {
  const configured = store.configured() && deviceTokenConfigured();
  if (!configured) {
    return jsonOk({
      service: "public-relay",
      status: "degraded",
      storage_configured: store.configured(),
      device_token_configured: deviceTokenConfigured(),
      door_checked_in: false,
      pending_bundles: 0,
    });
  }

  let doorCheckedIn = false;
  let pending = 0;
  let status = "ok";
  try {
    doorCheckedIn = (await store.getDoorKey()) !== null;
    pending = await store.pendingCount();
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
