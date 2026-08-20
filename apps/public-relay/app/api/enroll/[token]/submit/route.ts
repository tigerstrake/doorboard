/**
 * POST — accept one sealed bundle from a phone.
 *
 * The body is validated as an allow-list (E-8): there is no field a plaintext
 * photo or a display name could arrive under, so this endpoint cannot receive
 * them even by mistake. What lands in storage is exactly what arrived: AEAD
 * ciphertext plus opaque ids.
 *
 * The invite secret is checked against the stored hash purely as spam control.
 * Authorization is the Pi's job, and it re-verifies everything (ADR-0016 §4).
 */
import {
  RATE_LIMITS,
  clientAddress,
  digestsMatch,
  jsonError,
  jsonOk,
  sha256Base64Url,
} from "@/lib/device";
import { BUNDLE_TTL_S, getInvite, putBundle, storageConfigured, underRateLimit } from "@/lib/store";
import { InvalidBody, parseInviteToken, parseSealedBundle } from "@/lib/validate";

export const dynamic = "force-dynamic";

export async function POST(
  request: Request,
  context: { params: Promise<{ token: string }> },
): Promise<Response> {
  if (!storageConfigured()) return jsonError(503, "storage_not_configured");

  const { token } = await context.params;
  const parsed = parseInviteToken(token);
  if (!parsed) return jsonError(404, "invite_not_found");

  const perIp = RATE_LIMITS.submitPerIp;
  const perInvite = RATE_LIMITS.submitPerInvite;
  if (!(await underRateLimit("submit-ip", clientAddress(request), perIp.limit, perIp.windowS))) {
    return jsonError(429, "rate_limited");
  }
  if (
    !(await underRateLimit("submit-inv", parsed.inviteId, perInvite.limit, perInvite.windowS))
  ) {
    return jsonError(429, "rate_limited");
  }

  const invite = await getInvite(parsed.inviteId);
  if (!invite || !digestsMatch(invite.secret_sha256, sha256Base64Url(parsed.secret))) {
    return jsonError(404, "invite_not_found");
  }
  if (invite.consumed) return jsonError(409, "invite_already_used");
  if (Date.parse(invite.expires_at) <= Date.now()) return jsonError(410, "invite_expired");

  let bundle;
  try {
    bundle = parseSealedBundle(await request.json());
  } catch (error) {
    if (error instanceof InvalidBody) return jsonError(422, "invalid_bundle");
    return jsonError(400, "malformed_json");
  }

  if (bundle.invite_id !== parsed.inviteId) return jsonError(422, "invite_mismatch");
  // items = 1 manifest + N photos.
  if (bundle.items.length - 1 > invite.max_images) return jsonError(422, "too_many_images");

  await putBundle(bundle);

  return jsonOk(
    {
      bundle_id: bundle.bundle_id,
      status: "pending",
      expires_at: new Date(Date.now() + BUNDLE_TTL_S * 1000).toISOString(),
    },
    202,
  );
}
