/**
 * Runtime validation for relay request bodies (ADR-0016 E-8, E-13).
 *
 * Hand-written rather than schema-driven so the relay carries no validation
 * dependency, and deliberately **allow-list only**: an unknown field is a
 * rejection, not something quietly ignored. That is the mechanism keeping a
 * plaintext image or a display name from ever being accepted as a request field —
 * there is no key it could arrive under.
 */
import type { DoorKeyPublication, InviteRegistration, PickupAck, SealedBundle } from "./contracts";
import { SEAL_SUITE } from "./contracts";

const BASE64URL = /^[A-Za-z0-9_-]+$/;
const OPAQUE_ID = /^[a-z]{3,6}_[A-Za-z0-9]{16,32}$/;

const MAX_ITEMS = 16;
const MAX_CIPHERTEXT_CHARS = 1_400_000;
const MAX_CONSENT_CHARS = 32_000;

export class InvalidBody extends Error {}

function fail(message: string): never {
  throw new InvalidBody(message);
}

function object(value: unknown, allowed: readonly string[], where: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    fail(`${where} must be an object`);
  }
  const record = value as Record<string, unknown>;
  for (const key of Object.keys(record)) {
    if (!allowed.includes(key)) fail(`${where} has unexpected field '${key}'`);
  }
  return record;
}

function b64url(value: unknown, where: string, maxChars = 4096): string {
  if (typeof value !== "string" || value.length === 0) fail(`${where} must be a non-empty string`);
  if (value.length > maxChars) fail(`${where} exceeds ${maxChars} characters`);
  if (!BASE64URL.test(value)) fail(`${where} must be unpadded base64url`);
  return value;
}

function opaqueId(value: unknown, where: string): string {
  if (typeof value !== "string" || !OPAQUE_ID.test(value)) {
    fail(`${where} must be an opaque prefixed id`);
  }
  return value;
}

function isoTimestamp(value: unknown, where: string): string {
  if (typeof value !== "string" || Number.isNaN(Date.parse(value))) {
    fail(`${where} must be an ISO-8601 timestamp`);
  }
  return value;
}

function boundedInt(value: unknown, where: string, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max) {
    fail(`${where} must be an integer in [${min}, ${max}]`);
  }
  return value;
}

function boundedString(value: unknown, where: string, max: number): string {
  if (typeof value !== "string" || value.length === 0) fail(`${where} must be a non-empty string`);
  if (value.length > max) fail(`${where} exceeds ${max} characters`);
  return value;
}

export function parseSealedBundle(value: unknown): SealedBundle {
  const raw = object(
    value,
    ["v", "suite", "bundle_id", "invite_id", "door_key_id", "ephemeral_public_key", "salt", "items"],
    "bundle",
  );
  if (raw.v !== 1) fail("bundle.v must be 1");
  if (raw.suite !== SEAL_SUITE) fail("bundle.suite is not the supported seal suite");
  if (!Array.isArray(raw.items) || raw.items.length < 1 || raw.items.length > MAX_ITEMS) {
    fail(`bundle.items must hold between 1 and ${MAX_ITEMS} entries`);
  }

  const items = raw.items.map((entry, position) => {
    const item = object(entry, ["index", "nonce", "ciphertext"], `bundle.items[${position}]`);
    return {
      index: boundedInt(item.index, `bundle.items[${position}].index`, 0, MAX_ITEMS),
      nonce: b64url(item.nonce, `bundle.items[${position}].nonce`, 32),
      ciphertext: b64url(
        item.ciphertext,
        `bundle.items[${position}].ciphertext`,
        MAX_CIPHERTEXT_CHARS,
      ),
    };
  });

  const indexes = items.map((item) => item.index).sort((a, b) => a - b);
  if (indexes.some((index, position) => index !== position)) {
    fail("bundle.items indexes must be contiguous from 0");
  }

  return {
    v: 1,
    suite: SEAL_SUITE,
    bundle_id: opaqueId(raw.bundle_id, "bundle.bundle_id"),
    invite_id: opaqueId(raw.invite_id, "bundle.invite_id"),
    door_key_id: opaqueId(raw.door_key_id, "bundle.door_key_id"),
    ephemeral_public_key: b64url(raw.ephemeral_public_key, "bundle.ephemeral_public_key", 256),
    salt: b64url(raw.salt, "bundle.salt", 64),
    items,
  };
}

export function parseDoorKeyPublication(value: unknown): DoorKeyPublication {
  const raw = object(
    value,
    [
      "door_key_id",
      "suite",
      "public_key",
      "fingerprint",
      "consent_version",
      "consent_text",
      "published_at",
    ],
    "publication",
  );
  if (raw.suite !== SEAL_SUITE) fail("publication.suite is not the supported seal suite");
  return {
    door_key_id: opaqueId(raw.door_key_id, "publication.door_key_id"),
    suite: SEAL_SUITE,
    public_key: b64url(raw.public_key, "publication.public_key", 256),
    fingerprint: b64url(raw.fingerprint, "publication.fingerprint", 64),
    consent_version: boundedString(raw.consent_version, "publication.consent_version", 16),
    consent_text: boundedString(raw.consent_text, "publication.consent_text", MAX_CONSENT_CHARS),
    published_at: isoTimestamp(raw.published_at, "publication.published_at"),
  };
}

export function parseInviteRegistration(value: unknown): InviteRegistration {
  const raw = object(
    value,
    ["invite_id", "secret_sha256", "expires_at", "max_images"],
    "registration",
  );
  return {
    invite_id: opaqueId(raw.invite_id, "registration.invite_id"),
    secret_sha256: b64url(raw.secret_sha256, "registration.secret_sha256", 64),
    expires_at: isoTimestamp(raw.expires_at, "registration.expires_at"),
    max_images: boundedInt(raw.max_images, "registration.max_images", 1, 16),
  };
}

const ACK_OUTCOMES = ["enrolled", "failed", "rejected"] as const;

export function parsePickupAck(value: unknown): PickupAck {
  const raw = object(value, ["bundle_id", "outcome", "reason"], "ack");
  if (typeof raw.outcome !== "string" || !ACK_OUTCOMES.includes(raw.outcome as never)) {
    fail(`ack.outcome must be one of ${ACK_OUTCOMES.join(", ")}`);
  }
  let reason: string | null = null;
  if (raw.reason !== undefined && raw.reason !== null) {
    // Bounded so a reason cannot become a channel for smuggling user data back
    // through the relay.
    reason = boundedString(raw.reason, "ack.reason", 200);
  }
  return {
    bundle_id: opaqueId(raw.bundle_id, "ack.bundle_id"),
    outcome: raw.outcome as PickupAck["outcome"],
    reason,
  };
}

/** Split an invite token (`<invite_id>.<secret>`) without leaking either half. */
export function parseInviteToken(token: string): { inviteId: string; secret: string } | null {
  const dot = token.indexOf(".");
  if (dot <= 0 || dot === token.length - 1) return null;
  const inviteId = token.slice(0, dot);
  const secret = token.slice(dot + 1);
  if (!OPAQUE_ID.test(inviteId)) return null;
  if (!BASE64URL.test(secret) || secret.length < 16 || secret.length > 128) return null;
  return { inviteId, secret };
}
