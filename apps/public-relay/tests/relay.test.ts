/**
 * Relay behaviour tests (ADR-0016 P-13, P-15, plus the validation firewall E-8).
 *
 * The central claim under test is negative: whatever a phone sends, and whatever
 * the relay stores, no plaintext photo and no display name is ever recoverable
 * from this deployment.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { GET as getDoorKey, PUT as putDoorKey } from "@/app/api/door-key/route";
import { GET as getInviteState } from "@/app/api/enroll/[token]/route";
import { POST as submitBundle } from "@/app/api/enroll/[token]/submit/route";
import { GET as getHealth } from "@/app/api/health/route";
import { PUT as registerInvite } from "@/app/api/invite/route";
import { GET as pickup } from "@/app/api/pickup/route";
import { POST as ack } from "@/app/api/pickup/ack/route";
import { GET as getStatus } from "@/app/api/status/[bundleId]/route";
import { sha256Base64Url } from "@/lib/device";
import { KeyFingerprintMismatch, assertKeyMatchesFingerprint, fingerprintFor } from "@/lib/seal";
import { setRedisForTests } from "@/lib/store";
import { INVITE_SECRET_HEADER, InvalidBody, parseSealedBundle } from "@/lib/validate";

import { FakeRedis } from "./fakeRedis";
import {
  DEVICE_TOKEN,
  IMAGE_SENTINEL,
  NAME_SENTINEL,
  b64url,
  fakeDoorKey,
  makeSealedBundle,
  routeContext,
} from "./helpers";

let store: FakeRedis;

beforeEach(() => {
  store = new FakeRedis();
  setRedisForTests(store);
  process.env.RELAY_DEVICE_TOKEN = DEVICE_TOKEN;
  process.env.UPSTASH_REDIS_REST_URL = "https://fake.upstash.test";
  process.env.UPSTASH_REDIS_REST_TOKEN = "fake-token";
});

afterEach(() => {
  setRedisForTests(null);
});

function deviceRequest(url: string, method: string, body?: unknown): Request {
  return new Request(url, {
    method,
    headers: {
      authorization: `Bearer ${DEVICE_TOKEN}`,
      "content-type": "application/json",
      "x-forwarded-for": "203.0.113.7",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function publicRequest(
  url: string,
  method = "GET",
  body?: unknown,
  extraHeaders: Record<string, string> = {},
): Request {
  return new Request(url, {
    method,
    headers: { "content-type": "application/json", "x-forwarded-for": "198.51.100.4", ...extraHeaders },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/** The enroll secret now travels in a header, never the URL (ADR-0043 §2). */
function withSecret(secret: string): Record<string, string> {
  return { [INVITE_SECRET_HEADER]: secret };
}

async function seedDoorKeyAndInvite(): Promise<{ token: string; inviteId: string; secret: string }> {
  const key = await fakeDoorKey();
  const publishResp = await putDoorKey(
    deviceRequest("https://relay.test/api/door-key", "PUT", key.publication),
  );
  expect(publishResp.status).toBe(200);

  const inviteId = `inv_${"a".repeat(22)}`;
  const secret = "dGVzdC1pbnZpdGUtc2VjcmV0LXZhbHVl";
  const registerResp = await registerInvite(
    deviceRequest("https://relay.test/api/invite", "PUT", {
      invite_id: inviteId,
      secret_sha256: sha256Base64Url(secret),
      expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      max_images: 5,
    }),
  );
  expect(registerResp.status).toBe(200);

  // `token` is now just the invite id — the path segment. The secret rides in a header.
  return { token: inviteId, inviteId, secret };
}

// -- P-13: the relay never holds plaintext ---------------------------------

describe("P-13 the relay never sees plaintext", () => {
  it("stores no image bytes or display name anywhere, through a full submit", async () => {
    const { token, inviteId, secret } = await seedDoorKeyAndInvite();
    const key = await fakeDoorKey();
    const bundle = await makeSealedBundle({ key, inviteId });

    const resp = await submitBundle(
      publicRequest(`https://relay.test/api/enroll/${token}/submit`, "POST", bundle, withSecret(secret)),
      routeContext({ token }),
    );
    expect(resp.status).toBe(202);

    // Everything this deployment ever wrote, scanned for the sentinels.
    const written = store.dumpAll();
    expect(written).not.toContain(NAME_SENTINEL);
    expect(written).not.toContain(b64url(new TextEncoder().encode(IMAGE_SENTINEL)));
    expect(written).not.toContain(IMAGE_SENTINEL);
    // Sanity: it really did store the bundle, so the absence above means something.
    expect(written).toContain(bundle.bundle_id);
  });

  it("hands the Pi back exactly the ciphertext it was given, and nothing more", async () => {
    const { token, inviteId, secret } = await seedDoorKeyAndInvite();
    const key = await fakeDoorKey();
    const bundle = await makeSealedBundle({ key, inviteId });
    await submitBundle(
      publicRequest(`https://relay.test/api/enroll/${token}/submit`, "POST", bundle, withSecret(secret)),
      routeContext({ token }),
    );

    const resp = await pickup(deviceRequest("https://relay.test/api/pickup", "GET"));
    const body = (await resp.json()) as { items: Array<{ bundle: typeof bundle }> };
    expect(body.items).toHaveLength(1);
    expect(body.items[0]!.bundle).toEqual(bundle);
    expect(JSON.stringify(body)).not.toContain(NAME_SENTINEL);
  });

  it("keeps no ciphertext after the Pi acks", async () => {
    const { token, inviteId, secret } = await seedDoorKeyAndInvite();
    const key = await fakeDoorKey();
    const bundle = await makeSealedBundle({ key, inviteId });
    await submitBundle(
      publicRequest(`https://relay.test/api/enroll/${token}/submit`, "POST", bundle, withSecret(secret)),
      routeContext({ token }),
    );
    await pickup(deviceRequest("https://relay.test/api/pickup", "GET"));

    await ack(
      deviceRequest("https://relay.test/api/pickup/ack", "POST", {
        bundle_id: bundle.bundle_id,
        outcome: "enrolled",
        reason: null,
      }),
    );

    expect(await store.get(`bundle:${bundle.bundle_id}`)).toBeNull();
    // The status survives so the phone can be told, and carries no user data.
    const statusResp = await getStatus(
      publicRequest(`https://relay.test/api/status/${bundle.bundle_id}`),
      routeContext({ bundleId: bundle.bundle_id }),
    );
    const status = (await statusResp.json()) as { status: string };
    expect(status.status).toBe("enrolled");
    // And the invite is closed out, so a replay cannot be uploaded again.
    const second = await submitBundle(
      publicRequest(`https://relay.test/api/enroll/${token}/submit`, "POST", bundle, withSecret(secret)),
      routeContext({ token }),
    );
    expect(second.status).toBe(409);
  });
});

// -- P-15: fingerprint pinning --------------------------------------------

describe("P-15 fingerprint pinning refuses a substituted key", () => {
  it("accepts a key whose fingerprint matches the QR fragment", async () => {
    const key = await fakeDoorKey();
    await expect(
      assertKeyMatchesFingerprint(key.publication.public_key, key.publication.fingerprint),
    ).resolves.toBeUndefined();
  });

  it("throws when the relay serves a different key", async () => {
    const genuine = await fakeDoorKey();
    const substituted = await fakeDoorKey("attacker");
    expect(substituted.publication.public_key).not.toEqual(genuine.publication.public_key);

    await expect(
      assertKeyMatchesFingerprint(substituted.publication.public_key, genuine.publication.fingerprint),
    ).rejects.toBeInstanceOf(KeyFingerprintMismatch);
  });

  it("derives the fingerprint the same way the Pi does", async () => {
    const key = await fakeDoorKey();
    const recomputed = await fingerprintFor(key.publicKeyBytes);
    expect(recomputed).toEqual(key.publication.fingerprint);
    // 16 bytes of base64url, unpadded.
    expect(recomputed).toMatch(/^[A-Za-z0-9_-]{22}$/);
  });
});

// -- E-8: the request schema cannot carry plaintext -----------------------

describe("E-8 the submit schema admits no plaintext field", () => {
  it("rejects an unknown field rather than ignoring it", async () => {
    const key = await fakeDoorKey();
    const bundle = await makeSealedBundle({ key, inviteId: `inv_${"a".repeat(22)}` });
    expect(() =>
      parseSealedBundle({ ...bundle, display_name: "Tiger", images: ["rawbytes"] }),
    ).toThrow(InvalidBody);
  });

  it.each([
    ["padded base64", { salt: "c2FsdA==" }],
    ["non-base64url characters", { salt: "salt+with/chars" }],
    ["a name-shaped bundle id", { bundle_id: "tiger" }],
    ["the wrong suite", { suite: "rot13" }],
  ])("rejects %s", async (_label, override) => {
    const key = await fakeDoorKey();
    const bundle = await makeSealedBundle({ key, inviteId: `inv_${"a".repeat(22)}` });
    expect(() => parseSealedBundle({ ...bundle, ...override })).toThrow(InvalidBody);
  });

  it("rejects non-contiguous item indexes", async () => {
    const key = await fakeDoorKey();
    const bundle = await makeSealedBundle({ key, inviteId: `inv_${"a".repeat(22)}` });
    const gapped = {
      ...bundle,
      items: bundle.items.map((item, index) => ({ ...item, index: index === 0 ? 0 : index + 5 })),
    };
    expect(() => parseSealedBundle(gapped)).toThrow(InvalidBody);
  });
});

// -- E-14: no admin surface, device auth on every Pi route ----------------

describe("E-14 the relay exposes no privileged surface", () => {
  it.each([
    ["PUT", "/api/door-key", putDoorKey],
    ["PUT", "/api/invite", registerInvite],
    ["GET", "/api/pickup", pickup],
    ["POST", "/api/pickup/ack", ack],
  ])("requires the device token for %s %s", async (method, path, handler) => {
    // GET/HEAD cannot carry a body, so only send one for the mutating routes.
    const body = method === "GET" ? undefined : { bundle_id: "x", outcome: "enrolled" };
    const resp = await (handler as (r: Request) => Promise<Response>)(
      publicRequest(`https://relay.test${path}`, method, body),
    );
    expect(resp.status).toBe(401);
  });

  it("rejects a wrong device token", async () => {
    const resp = await pickup(
      new Request("https://relay.test/api/pickup", {
        headers: { authorization: "Bearer not-the-token" },
      }),
    );
    expect(resp.status).toBe(401);
  });

  it("reports health without revealing who is enrolled", async () => {
    await seedDoorKeyAndInvite();
    const body = (await (await getHealth()).json()) as Record<string, unknown>;
    expect(body).toEqual({
      service: "public-relay",
      status: "ok",
      storage_configured: true,
      device_token_configured: true,
      door_checked_in: true,
      pending_bundles: 0,
    });
  });
});

// -- invite state disclosure ---------------------------------------------

describe("invite state tells a phone only about its own invite", () => {
  it("reports open for a valid unused invite", async () => {
    const { token, inviteId, secret } = await seedDoorKeyAndInvite();
    const resp = await getInviteState(
      publicRequest(`https://relay.test/api/enroll/${token}`, "GET", undefined, withSecret(secret)),
      routeContext({ token }),
    );
    const body = (await resp.json()) as { status: string; invite_id: string; max_images: number };
    expect(body.status).toBe("open");
    expect(body.invite_id).toBe(inviteId);
    expect(body.max_images).toBe(5);
  });

  it("cannot be used to enumerate invite ids", async () => {
    const { inviteId } = await seedDoorKeyAndInvite();

    // A real invite id with the wrong secret must look exactly like a made-up one.
    const wrong = "d3Jvbmctc2VjcmV0LXZhbHVl";
    const wrongSecret = await getInviteState(
      publicRequest(`https://relay.test/api/enroll/${inviteId}`, "GET", undefined, withSecret(wrong)),
      routeContext({ token: inviteId }),
    );
    const madeUpId = `inv_${"z".repeat(22)}`;
    const madeUp = await getInviteState(
      publicRequest(`https://relay.test/api/enroll/${madeUpId}`, "GET", undefined, withSecret(wrong)),
      routeContext({ token: madeUpId }),
    );

    expect(await wrongSecret.json()).toEqual(await madeUp.json());
  });

  it("P-32: ignores a secret carried in the path (the old, leaky form)", async () => {
    // The pre-ADR-0043 link put the secret in the path: `/e/<inviteId>.<secret>`. That form
    // must no longer authenticate — a `<id>.<secret>` path segment is not a valid invite id,
    // and with no secret header there is nothing to check, so the invite reads `unknown`.
    const { inviteId, secret } = await seedDoorKeyAndInvite();
    const pathToken = `${inviteId}.${secret}`;
    const resp = await getInviteState(
      publicRequest(`https://relay.test/api/enroll/${pathToken}`),
      routeContext({ token: pathToken }),
    );
    const body = (await resp.json()) as { status: string; invite_id: string };
    expect(body.status).toBe("unknown");
    expect(body.invite_id).toBe("");
  });

  it("refuses a submit whose invite_id disagrees with the URL", async () => {
    const { token, secret } = await seedDoorKeyAndInvite();
    const key = await fakeDoorKey();
    const bundle = await makeSealedBundle({ key, inviteId: `inv_${"b".repeat(22)}` });

    const resp = await submitBundle(
      publicRequest(`https://relay.test/api/enroll/${token}/submit`, "POST", bundle, withSecret(secret)),
      routeContext({ token }),
    );
    expect(resp.status).toBe(422);
  });

  it("refuses more photos than the invite allows", async () => {
    const key = await fakeDoorKey();
    await putDoorKey(deviceRequest("https://relay.test/api/door-key", "PUT", key.publication));
    const inviteId = `inv_${"c".repeat(22)}`;
    const secret = "dGVzdC1pbnZpdGUtc2VjcmV0LXZhbHVl";
    await registerInvite(
      deviceRequest("https://relay.test/api/invite", "PUT", {
        invite_id: inviteId,
        secret_sha256: sha256Base64Url(secret),
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
        max_images: 1,
      }),
    );
    const token = inviteId;
    const bundle = await makeSealedBundle({ key, inviteId, imageCount: 3 });

    const resp = await submitBundle(
      publicRequest(`https://relay.test/api/enroll/${token}/submit`, "POST", bundle, withSecret(secret)),
      routeContext({ token }),
    );
    expect(resp.status).toBe(422);
    expect((await resp.json()) as unknown).toEqual({ error: "too_many_images" });
  });
});

// -- lease semantics -----------------------------------------------------

describe("pickup leasing", () => {
  it("does not hand the same bundle to a second poll inside the lease", async () => {
    const { token, inviteId, secret } = await seedDoorKeyAndInvite();
    const key = await fakeDoorKey();
    const bundle = await makeSealedBundle({ key, inviteId });
    await submitBundle(
      publicRequest(`https://relay.test/api/enroll/${token}/submit`, "POST", bundle, withSecret(secret)),
      routeContext({ token }),
    );

    const first = (await (await pickup(deviceRequest("https://relay.test/api/pickup", "GET"))).json()) as {
      items: unknown[];
    };
    const second = (await (await pickup(deviceRequest("https://relay.test/api/pickup", "GET"))).json()) as {
      items: unknown[];
    };

    expect(first.items).toHaveLength(1);
    expect(second.items).toHaveLength(0);
  });

  it("does not let a late duplicate ack undo an enrollment", async () => {
    const { token, inviteId, secret } = await seedDoorKeyAndInvite();
    const key = await fakeDoorKey();
    const bundle = await makeSealedBundle({ key, inviteId });
    await submitBundle(
      publicRequest(`https://relay.test/api/enroll/${token}/submit`, "POST", bundle, withSecret(secret)),
      routeContext({ token }),
    );
    await pickup(deviceRequest("https://relay.test/api/pickup", "GET"));

    for (const outcome of ["enrolled", "rejected"]) {
      await ack(
        deviceRequest("https://relay.test/api/pickup/ack", "POST", {
          bundle_id: bundle.bundle_id,
          outcome,
          reason: outcome === "rejected" ? "invite_already_consumed" : null,
        }),
      );
    }

    const statusResp = await getStatus(
      publicRequest(`https://relay.test/api/status/${bundle.bundle_id}`),
      routeContext({ bundleId: bundle.bundle_id }),
    );
    expect(((await statusResp.json()) as { status: string }).status).toBe("enrolled");
  });
});

describe("door key availability", () => {
  it("tells a phone the door has not checked in rather than serving nothing", async () => {
    const resp = await getDoorKey();
    expect(resp.status).toBe(503);
    expect((await resp.json()) as unknown).toEqual({ error: "door_key_unavailable" });
  });
});
