/**
 * Visitor relay behaviour (ADR-0017 P-21, P-22, plus the E-15/E-18 boundaries).
 *
 * The claims under test are about *scope*, not secrecy: this surface deliberately
 * handles plaintext, so what has to hold is that it handles only the enumerated
 * public fields, never a usable credential, and never the other scope's routes.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { GET as getDoorKey, PUT as putDoorKey } from "@/app/api/door-key/route";
import { PUT as registerInvite } from "@/app/api/invite/route";
import { GET as pickupEnrollment } from "@/app/api/pickup/route";
import { POST as ackEnrollment } from "@/app/api/pickup/ack/route";
import { GET as readVisitor } from "@/app/api/visitor/[token]/route";
import { POST as writeVisitor } from "@/app/api/visitor/[token]/action/route";
import { GET as pickupVisitor } from "@/app/api/visitor/pickup/route";
import { POST as ackVisitor } from "@/app/api/visitor/pickup/ack/route";
import { PUT as pushSnapshot } from "@/app/api/visitor/session/route";
import { sha256Base64Url } from "@/lib/device";
import { setRedisForTests } from "@/lib/store";

import { FakeRedis } from "./fakeRedis";
import { DEVICE_TOKEN, routeContext } from "./helpers";

const VISITOR_TOKEN = "eyJ2IjoxfQ.c2lnbmF0dXJl";
const VISITOR_DEVICE_TOKEN = "test-visitor-device-token";
const SESSION_ID = `ses_${"a".repeat(22)}`;
const NOTE_SENTINEL = "DOORBOARD_SENTINEL_NOTE_TEXT";

let store: FakeRedis;

beforeEach(() => {
  store = new FakeRedis();
  setRedisForTests(store);
  process.env.RELAY_DEVICE_TOKEN = DEVICE_TOKEN;
  process.env.RELAY_VISITOR_DEVICE_TOKEN = VISITOR_DEVICE_TOKEN;
  process.env.UPSTASH_REDIS_REST_URL = "https://fake.upstash.test";
  process.env.UPSTASH_REDIS_REST_TOKEN = "fake-token";
});

afterEach(() => setRedisForTests(null));

function req(
  url: string,
  method: string,
  options: { body?: unknown; token?: string; ip?: string } = {},
): Request {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    "x-forwarded-for": options.ip ?? "198.51.100.9",
  };
  if (options.token) headers.authorization = `Bearer ${options.token}`;
  return new Request(url, {
    method,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
}

function snapshotBody(overrides: Record<string, unknown> = {}) {
  return {
    session_token_sha256: sha256Base64Url(VISITOR_TOKEN),
    session_id: SESSION_ID,
    state: "UNANSWERED_TIMEOUT",
    expires_at: new Date(Date.now() + 300_000).toISOString(),
    poll: {
      poll_id: "poll_1",
      question: "Best doorbell sound?",
      options: [
        { option_id: "opt_a", label: "Chime" },
        { option_id: "opt_b", label: "Buzz" },
      ],
    },
    poll_results: null,
    outcomes: [],
    pushed_at: new Date().toISOString(),
    ...overrides,
  };
}

async function seedSnapshot(overrides: Record<string, unknown> = {}): Promise<void> {
  const resp = await pushSnapshot(
    req("https://relay.test/api/visitor/session", "PUT", {
      body: snapshotBody(overrides),
      token: VISITOR_DEVICE_TOKEN,
    }),
  );
  expect(resp.status).toBe(200);
}

// -- P-21: no usable credential on the relay -------------------------------

describe("P-21 the relay never holds a usable visitor token", () => {
  it("stores only the hash, yet still authorises the phone", async () => {
    await seedSnapshot();

    const everything = store.dumpAll();
    expect(everything).not.toContain(VISITOR_TOKEN);
    expect(everything).toContain(sha256Base64Url(VISITOR_TOKEN));

    const resp = await readVisitor(
      req(`https://relay.test/api/visitor/${VISITOR_TOKEN}`, "GET"),
      routeContext({ token: VISITOR_TOKEN }),
    );
    expect(resp.status).toBe(200);
  });

  it("does not hand the token hash back to the phone", async () => {
    await seedSnapshot();
    const body = (await (
      await readVisitor(
        req(`https://relay.test/api/visitor/${VISITOR_TOKEN}`, "GET"),
        routeContext({ token: VISITOR_TOKEN }),
      )
    ).json()) as Record<string, unknown>;

    expect(Object.keys(body).sort()).toEqual([
      // `attributed` is a boolean (ADR-0044): the page discloses that a write will be
      // attributed, but the relay never receives the recognised person's name.
      "attributed",
      "expires_at",
      "outcomes",
      "poll",
      "poll_results",
      "pushed_at",
      "session_id",
      "state",
    ]);
    expect(body).not.toHaveProperty("session_token_sha256");
    expect(body).not.toHaveProperty("attributed_to");
  });

  it("rejects a wrong token indistinguishably from an unknown one", async () => {
    await seedSnapshot();
    const wrong = await readVisitor(
      req("https://relay.test/api/visitor/nope.nope", "GET"),
      routeContext({ token: "nope.nope" }),
    );
    const unknown = await readVisitor(
      req("https://relay.test/api/visitor/other.other", "GET"),
      routeContext({ token: "other.other" }),
    );
    expect(wrong.status).toBe(404);
    expect(await wrong.json()).toEqual(await unknown.json());
  });
});

// -- P-22: scopes are disjoint --------------------------------------------

describe("P-22 device token scopes are disjoint", () => {
  it("refuses the enrollment token on visitor routes", async () => {
    for (const handler of [pushSnapshot, pickupVisitor, ackVisitor]) {
      const resp = await (handler as (r: Request) => Promise<Response>)(
        req("https://relay.test/api/visitor/x", "PUT", {
          body: snapshotBody(),
          token: DEVICE_TOKEN,
        }),
      );
      expect(resp.status).toBe(401);
    }
  });

  it("refuses the visitor token on enrollment routes", async () => {
    const key = await (await import("./helpers")).fakeDoorKey();
    const publish = await putDoorKey(
      req("https://relay.test/api/door-key", "PUT", {
        body: key.publication,
        token: VISITOR_DEVICE_TOKEN,
      }),
    );
    expect(publish.status).toBe(401);

    for (const handler of [registerInvite, pickupEnrollment, ackEnrollment]) {
      const resp = await (handler as (r: Request) => Promise<Response>)(
        req("https://relay.test/api/x", "POST", {
          body: { bundle_id: `bnd_${"a".repeat(22)}`, outcome: "enrolled" },
          token: VISITOR_DEVICE_TOKEN,
        }),
      );
      expect(resp.status).toBe(401);
    }
    // Sanity: the door key was never published, so this is a real refusal.
    expect((await getDoorKey()).status).toBe(503);
  });
});

// -- E-15: only the allow-listed snapshot is accepted ---------------------

describe("E-15 the snapshot is an allow-list", () => {
  it("rejects a snapshot carrying an identity field", async () => {
    const resp = await pushSnapshot(
      req("https://relay.test/api/visitor/session", "PUT", {
        body: { ...snapshotBody(), display_name: "Tiger", person_id: "prs_x" },
        token: VISITOR_DEVICE_TOKEN,
      }),
    );
    expect(resp.status).toBe(422);
  });

  it("rejects an unknown session state", async () => {
    const resp = await pushSnapshot(
      req("https://relay.test/api/visitor/session", "PUT", {
        body: snapshotBody({ state: "ADMIN_DIAGNOSTICS" }),
        token: VISITOR_DEVICE_TOKEN,
      }),
    );
    expect(resp.status).toBe(422);
  });
});

// -- the write path -------------------------------------------------------

describe("visitor writes queue for the Pi", () => {
  it("queues a note and hands it to door-api, then reflects the outcome", async () => {
    await seedSnapshot();

    const submit = await writeVisitor(
      req(`https://relay.test/api/visitor/${VISITOR_TOKEN}/action`, "POST", {
        body: { kind: "note", text: NOTE_SENTINEL },
      }),
      routeContext({ token: VISITOR_TOKEN }),
    );
    expect(submit.status).toBe(202);
    const { action_id: actionId } = (await submit.json()) as { action_id: string };

    const collected = (await (
      await pickupVisitor(
        req("https://relay.test/api/visitor/pickup", "GET", { token: VISITOR_DEVICE_TOKEN }),
      )
    ).json()) as { items: Array<{ action_id: string; note: { text: string } | null }> };
    expect(collected.items).toHaveLength(1);
    expect(collected.items[0]!.note?.text).toBe(NOTE_SENTINEL);

    await ackVisitor(
      req("https://relay.test/api/visitor/pickup/ack", "POST", {
        token: VISITOR_DEVICE_TOKEN,
        body: {
          outcomes: [
            {
              action_id: actionId,
              session_id: SESSION_ID,
              kind: "note",
              status: "applied",
              reason: null,
              entry_id: "gb_1",
            },
          ],
        },
      }),
    );

    const after = (await (
      await readVisitor(
        req(`https://relay.test/api/visitor/${VISITOR_TOKEN}`, "GET"),
        routeContext({ token: VISITOR_TOKEN }),
      )
    ).json()) as { outcomes: Array<{ action_id: string; status: string; entry_id: string }> };
    expect(after.outcomes).toHaveLength(1);
    expect(after.outcomes[0]).toMatchObject({ action_id: actionId, status: "applied", entry_id: "gb_1" });
  });

  it("refuses writes once the session has ended", async () => {
    await seedSnapshot({ state: "SESSION_END" });
    const resp = await writeVisitor(
      req(`https://relay.test/api/visitor/${VISITOR_TOKEN}/action`, "POST", {
        body: { kind: "note", text: "too late" },
      }),
      routeContext({ token: VISITOR_TOKEN }),
    );
    expect(resp.status).toBe(409);
    expect(await resp.json()).toEqual({ error: "session_not_writable" });
  });

  it("refuses writes for an expired session", async () => {
    await seedSnapshot({ expires_at: new Date(Date.now() - 1000).toISOString() });
    const resp = await writeVisitor(
      req(`https://relay.test/api/visitor/${VISITOR_TOKEN}/action`, "POST", {
        body: { kind: "note", text: "expired" },
      }),
      routeContext({ token: VISITOR_TOKEN }),
    );
    expect(resp.status).toBe(410);
  });

  it("rejects an over-long note rather than truncating it", async () => {
    await seedSnapshot();
    const resp = await writeVisitor(
      req(`https://relay.test/api/visitor/${VISITOR_TOKEN}/action`, "POST", {
        body: { kind: "note", text: "x".repeat(501) },
      }),
      routeContext({ token: VISITOR_TOKEN }),
    );
    expect(resp.status).toBe(422);
  });

  it("rejects an unknown write kind", async () => {
    await seedSnapshot();
    const resp = await writeVisitor(
      req(`https://relay.test/api/visitor/${VISITOR_TOKEN}/action`, "POST", {
        body: { kind: "unlock_the_door", text: "nice try" },
      }),
      routeContext({ token: VISITOR_TOKEN }),
    );
    expect(resp.status).toBe(422);
  });

  it("leases actions so two polls do not both collect one write", async () => {
    await seedSnapshot();
    await writeVisitor(
      req(`https://relay.test/api/visitor/${VISITOR_TOKEN}/action`, "POST", {
        body: { kind: "vote", poll_id: "poll_1", option_id: "opt_a" },
      }),
      routeContext({ token: VISITOR_TOKEN }),
    );

    const first = (await (
      await pickupVisitor(
        req("https://relay.test/api/visitor/pickup", "GET", { token: VISITOR_DEVICE_TOKEN }),
      )
    ).json()) as { items: unknown[] };
    const second = (await (
      await pickupVisitor(
        req("https://relay.test/api/visitor/pickup", "GET", { token: VISITOR_DEVICE_TOKEN }),
      )
    ).json()) as { items: unknown[] };

    expect(first.items).toHaveLength(1);
    expect(second.items).toHaveLength(0);
  });

  it("rate-limits a session that floods writes", async () => {
    await seedSnapshot();
    const statuses: number[] = [];
    for (let i = 0; i < 25; i += 1) {
      const resp = await writeVisitor(
        req(`https://relay.test/api/visitor/${VISITOR_TOKEN}/action`, "POST", {
          body: { kind: "note", text: `spam ${i}` },
        }),
        routeContext({ token: VISITOR_TOKEN }),
      );
      statuses.push(resp.status);
    }
    expect(statuses).toContain(429);
  });
});
