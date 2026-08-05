/**
 * GET — door-api collects queued visitor actions (ADR-0017).
 * POST /ack lives beside this; see ./ack/route.ts.
 *
 * Leased rather than deleted, so a door-api that dies mid-apply retries after the
 * lease lapses. Duplicate delivery is made harmless by door-api applying each
 * action_id at most once (P-23), not by hoping it never happens.
 */
import { isDeviceRequest, jsonError, jsonOk } from "@/lib/device";
import { leaseVisitorActions, storageConfigured } from "@/lib/store";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  if (!isDeviceRequest(request, "visitor")) return jsonError(401, "device_auth_required");
  if (!storageConfigured()) return jsonError(503, "storage_not_configured");

  return jsonOk({ items: await leaseVisitorActions() });
}
