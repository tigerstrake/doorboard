/**
 * Device authentication and request helpers (ADR-0016 E-14, ADR-0017 E-17).
 *
 * A device token authenticates **one Pi service to the relay**, and nothing else.
 * Neither token is a decryption key: an attacker holding the enrollment token can
 * collect ciphertext they cannot open and can deny service. That is the whole
 * blast radius, by design (E-9).
 *
 * The two scopes are deliberately separate credentials rather than one shared
 * secret, because door-api and door-visiond have different blast radii. A stolen
 * door-api token must not be able to pull enrollment bundles, and a stolen
 * door-visiond token must not be able to post visitor content. Every route
 * demands exactly one scope; a route that accepts either is a review-blocking
 * defect (E-17).
 *
 * There is deliberately no route anywhere in this app that lists enrollees, mints
 * an invite, or reaches back toward the Pi. Every exchange is Pi-initiated.
 */
import { createHash, timingSafeEqual } from "node:crypto";

export const RATE_LIMITS = {
  /** Reading an invite's public state, per IP. */
  inviteLookup: { limit: 60, windowS: 300 },
  /** Submitting a sealed bundle, per invite — one real enrollment needs one. */
  submitPerInvite: { limit: 5, windowS: 900 },
  /** Submitting, per IP, so one host cannot grind through many invites. */
  submitPerIp: { limit: 12, windowS: 900 },
  /** Status polling, per IP. The client polls every 2s while it waits. */
  status: { limit: 300, windowS: 300 },
  /** A visitor reading their own session snapshot; the page polls every ~2 s. */
  visitorRead: { limit: 300, windowS: 300 },
  /** Visitor writes per session — a note, a vote, maybe a retry. Not a firehose. */
  visitorWritePerSession: { limit: 20, windowS: 900 },
  /** Visitor writes per IP, so one host cannot grind through sessions. */
  visitorWritePerIp: { limit: 40, windowS: 900 },
} as const;

/** Which Pi service a route belongs to. */
export type DeviceScope = "enrollment" | "visitor";

const SCOPE_ENV: Record<DeviceScope, string> = {
  enrollment: "RELAY_DEVICE_TOKEN",
  visitor: "RELAY_VISITOR_DEVICE_TOKEN",
};

export function deviceTokenConfigured(scope: DeviceScope = "enrollment"): boolean {
  return Boolean(process.env[SCOPE_ENV[scope]]);
}

/**
 * Constant-time bearer check against exactly one scope's token.
 *
 * Checking one scope rather than "any known token" is the point: it is what makes
 * the two credentials genuinely separate rather than interchangeable.
 */
export function isDeviceRequest(request: Request, scope: DeviceScope = "enrollment"): boolean {
  const expected = process.env[SCOPE_ENV[scope]];
  if (!expected) return false;
  const header = request.headers.get("authorization") ?? "";
  if (!header.startsWith("Bearer ")) return false;
  const presented = header.slice("Bearer ".length);
  // Hash both sides so timingSafeEqual never sees mismatched lengths (which it
  // throws on, and which would itself leak the expected length).
  const a = createHash("sha256").update(presented).digest();
  const b = createHash("sha256").update(expected).digest();
  return timingSafeEqual(a, b);
}

export function sha256Base64Url(value: string): string {
  return createHash("sha256").update(value).digest("base64url");
}

/** Compare two base64url digests without leaking where they diverge. */
export function digestsMatch(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}

/** Characters of base64url SHA-256 kept as a rate-limit subject: 16 bytes, plenty to bucket by. */
const ADDRESS_DIGEST_CHARS = 22;

/**
 * The caller's address as a rate-limit subject — a truncated one-way digest, never the
 * address itself, so no IP is ever at rest in the `rate_limit` table.
 *
 * On Cloudflare the trustworthy header is `CF-Connecting-IP`, which the edge sets from the
 * TCP peer. `X-Forwarded-For` is *not* usable here: Cloudflare appends to a client-supplied
 * value rather than replacing it, so its leftmost entry is whatever the client chose — which
 * let every per-IP limit be bypassed by rotating it (and let an 8 kB header become an 8 kB
 * row). A missing header buckets everyone together, which fails safe (stricter, never looser).
 */
export function clientAddress(request: Request): string {
  const address = request.headers.get("cf-connecting-ip")?.trim();
  return sha256Base64Url(address && address.length > 0 ? address : "unknown").slice(
    0,
    ADDRESS_DIGEST_CHARS,
  );
}

export function jsonError(status: number, error: string, extra?: Record<string, unknown>): Response {
  return Response.json({ error, ...extra }, { status, headers: { "cache-control": "no-store" } });
}

export function jsonOk(body: unknown, status = 200): Response {
  return Response.json(body, { status, headers: { "cache-control": "no-store" } });
}
