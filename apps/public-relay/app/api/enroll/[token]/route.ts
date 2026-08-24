/**
 * GET — what a phone may learn about its own invite before sealing.
 *
 * Returns state only: open/consumed/expired/unknown, and how many photos are
 * allowed. No name, no door detail, nothing about anyone else. An unknown invite
 * and a bad secret return the same `unknown` state so this cannot be used to
 * enumerate valid invite ids.
 */
import { RATE_LIMITS, clientAddress, digestsMatch, jsonError, jsonOk, sha256Base64Url } from "@/lib/device";
import { getInvite, storageConfigured, underRateLimit } from "@/lib/store";
import { INVITE_SECRET_HEADER, parseInviteId, parseInviteSecret } from "@/lib/validate";

export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  context: { params: Promise<{ token: string }> },
): Promise<Response> {
  if (!storageConfigured()) return jsonError(503, "storage_not_configured");

  const limit = RATE_LIMITS.inviteLookup;
  if (!(await underRateLimit("invite", clientAddress(request), limit.limit, limit.windowS))) {
    return jsonError(429, "rate_limited");
  }

  // The path segment is the invite id; the secret arrives in a header (ADR-0043 §2), never a URL.
  const { token: inviteId } = await context.params;
  const secret = parseInviteSecret(request.headers.get(INVITE_SECRET_HEADER));
  if (!parseInviteId(inviteId) || !secret) {
    return jsonOk({ invite_id: "", status: "unknown", max_images: 1 });
  }

  const invite = await getInvite(inviteId);
  if (!invite || !digestsMatch(invite.secret_sha256, sha256Base64Url(secret))) {
    // Deliberately indistinguishable from a wrong secret.
    return jsonOk({ invite_id: "", status: "unknown", max_images: 1 });
  }

  const expired = Date.parse(invite.expires_at) <= Date.now();
  const status = invite.consumed ? "consumed" : expired ? "expired" : "open";

  return jsonOk({
    invite_id: inviteId,
    status,
    max_images: invite.max_images,
    expires_at: invite.expires_at,
  });
}
