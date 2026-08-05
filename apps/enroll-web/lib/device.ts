/**
 * Device authentication and request helpers (ADR-0016 E-14).
 *
 * The device token authenticates **the Pi to the relay**, and nothing else. It is
 * not a decryption key and grants no ability to read a bundle: an attacker
 * holding it can collect ciphertext they cannot open, and can deny service. That
 * is the whole blast radius, by design (E-9).
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
} as const;

export function deviceTokenConfigured(): boolean {
  return Boolean(process.env.RELAY_DEVICE_TOKEN);
}

/** Constant-time bearer check against `RELAY_DEVICE_TOKEN`. */
export function isDeviceRequest(request: Request): boolean {
  const expected = process.env.RELAY_DEVICE_TOKEN;
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

/**
 * Best-effort client address for rate limiting. Behind Vercel the leftmost
 * `x-forwarded-for` entry is the caller; a missing header buckets everyone
 * together, which fails safe (stricter, never looser).
 */
export function clientAddress(request: Request): string {
  const forwarded = request.headers.get("x-forwarded-for");
  const first = forwarded?.split(",")[0]?.trim();
  return first && first.length > 0 ? first : "unknown";
}

export function jsonError(status: number, error: string, extra?: Record<string, unknown>): Response {
  return Response.json({ error, ...extra }, { status, headers: { "cache-control": "no-store" } });
}

export function jsonOk(body: unknown, status = 200): Response {
  return Response.json(body, { status, headers: { "cache-control": "no-store" } });
}
