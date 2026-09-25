/**
 * GET /api/health — reachability to the public; configuration detail to the door only.
 *
 * The public body is `{service, status}` and nothing else. Whether the door has checked in
 * and how many bundles are pending is "someone at this house is enrolling right now" to
 * anyone who polls a public URL on an untrusted host (ARCHITECTURE.md §2) — ADR-0016 §1
 * accepts that metadata being visible to a *compromised* relay, not to the internet. The
 * detail is returned only to the door-visiond device token: one scope, not either (E-17).
 */
import { deviceTokenConfigured, isDeviceRequest, jsonOk } from "@/lib/device";
import type { RelayStore } from "@/lib/relayTypes";

export async function handleGet(request: Request, store: RelayStore): Promise<Response> {
  const configured = store.configured() && deviceTokenConfigured();
  if (!isDeviceRequest(request)) {
    return jsonOk({ service: "public-relay", status: configured ? "ok" : "degraded" });
  }

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
