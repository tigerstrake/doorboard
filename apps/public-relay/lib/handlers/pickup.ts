/**
 * GET — the Pi collects sealed bundles (ADR-0016 §6).
 *
 * This is the only way ciphertext leaves the relay, and it is always the Pi
 * asking: there is no inbound path from here to the door, which is what lets the
 * whole thing work behind NAT with no open port.
 *
 * Bundles are leased, not deleted, so a Pi that dies mid-enrollment retries after
 * the lease lapses. A duplicate delivery is harmless because the invite is
 * single-use (E-11).
 */
import { isDeviceRequest, jsonError, jsonOk } from "@/lib/device";
import type { RelayStore } from "@/lib/relayTypes";

export async function handleGet(request: Request, store: RelayStore): Promise<Response> {
  if (!isDeviceRequest(request)) return jsonError(401, "device_auth_required");
  if (!store.configured()) return jsonError(503, "storage_not_configured");

  const leased = await store.leaseBundles();
  return jsonOk({
    items: leased.map((record) => ({
      bundle: record.bundle,
      submitted_at: record.submitted_at,
    })),
  });
}
