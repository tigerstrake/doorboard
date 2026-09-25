/**
 * Retention on D1 (ADR-0016 §5 windows, preserved by ADR-0043 §1) — the D1 twin of P-13/P-21.
 *
 * Every read already filters on expiry, so nothing stale is *served*. This file is about what
 * is *held*: ciphertext the Pi never collected, a note door-api never picked up, a rate-limit
 * bucket. Pages Functions have no cron, so the sweep runs from the Pi's pickup polls; the
 * claim under test is that once a poll has run past the windows, a byte-scan of every row
 * finds none of it.
 */
import { beforeEach, describe, expect, it } from "vitest";

import { D1RelayStore } from "@/lib/d1/d1Store";
import { sha256Base64Url } from "@/lib/device";
import { handlePut as putDoorKey } from "@/lib/handlers/doorKey";
import { handlePost as submitBundle } from "@/lib/handlers/enrollSubmit";
import { handlePut as registerInvite } from "@/lib/handlers/invite";
import { handleGet as pickup } from "@/lib/handlers/pickup";
import { handlePost as writeVisitor } from "@/lib/handlers/visitorAction";
import { handleGet as pickupVisitor } from "@/lib/handlers/visitorPickup";
import { handlePut as pushSnapshot } from "@/lib/handlers/visitorSession";
import { INVITE_SECRET_HEADER } from "@/lib/validate";

import { DEVICE_TOKEN, fakeDoorKey, makeSealedBundle } from "./helpers";
import { NodeSqliteD1 } from "./nodeSqliteD1";

const VISITOR_DEVICE_TOKEN = "test-visitor-device-token";
const VISITOR_TOKEN = "eyJ2IjoxfQ.c2lnbmF0dXJl";
const SESSION_ID = `ses_${"a".repeat(22)}`;
const NOTE_SENTINEL = "DOORBOARD_SENTINEL_NOTE_TEXT";
const PHONE_IP = "203.0.113.5";
const INVITE_ID = `inv_${"a".repeat(22)}`;
const INVITE_SECRET = "dGVzdC1pbnZpdGUtc2VjcmV0LXZhbHVl";
const DAY_MS = 24 * 3_600_000;

const clock = { ms: 0 };
let db: NodeSqliteD1;
let store: D1RelayStore;

beforeEach(async () => {
  clock.ms = Date.now();
  db = new NodeSqliteD1();
  store = new D1RelayStore(db, () => clock.ms);
  await store.migrate();
  process.env.RELAY_DEVICE_TOKEN = DEVICE_TOKEN;
  process.env.RELAY_VISITOR_DEVICE_TOKEN = VISITOR_DEVICE_TOKEN;
});

function device(token: string, url: string, method: string, body?: unknown): Request {
  return new Request(url, {
    method,
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function phone(url: string, method: string, body: unknown, extra: Record<string, string> = {}): Request {
  return new Request(url, {
    method,
    headers: { "content-type": "application/json", "cf-connecting-ip": PHONE_IP, ...extra },
    body: JSON.stringify(body),
  });
}

/** An uncollected sealed bundle; returns the ciphertext of one photo, to scan for. */
async function leaveUncollectedBundle(): Promise<{ bundleId: string; ciphertext: string }> {
  const key = await fakeDoorKey();
  await putDoorKey(device(DEVICE_TOKEN, "https://relay.test/api/door-key", "PUT", key.publication), store);
  await registerInvite(
    device(DEVICE_TOKEN, "https://relay.test/api/invite", "PUT", {
      invite_id: INVITE_ID,
      secret_sha256: sha256Base64Url(INVITE_SECRET),
      expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      max_images: 5,
    }),
    store,
  );
  const bundle = await makeSealedBundle({ key, inviteId: INVITE_ID });
  const resp = await submitBundle(
    phone(`https://relay.test/api/enroll/${INVITE_ID}/submit`, "POST", bundle, {
      [INVITE_SECRET_HEADER]: INVITE_SECRET,
    }),
    store,
    { token: INVITE_ID },
  );
  expect(resp.status).toBe(202);
  return { bundleId: bundle.bundle_id, ciphertext: bundle.items[1]!.ciphertext };
}

/** A snapshot plus a queued note door-api never collects. */
async function leaveUncollectedNote(): Promise<void> {
  const pushed = await pushSnapshot(
    device(VISITOR_DEVICE_TOKEN, "https://relay.test/api/visitor/session", "PUT", {
      session_token_sha256: sha256Base64Url(VISITOR_TOKEN),
      session_id: SESSION_ID,
      state: "UNANSWERED_TIMEOUT",
      expires_at: new Date(Date.now() + 300_000).toISOString(),
      poll: null,
      poll_results: null,
      outcomes: [],
      pushed_at: new Date().toISOString(),
    }),
    store,
  );
  expect(pushed.status).toBe(200);
  const queued = await writeVisitor(
    phone(`https://relay.test/api/visitor/${VISITOR_TOKEN}/action`, "POST", {
      kind: "note",
      text: NOTE_SENTINEL,
    }),
    store,
    { token: VISITOR_TOKEN },
  );
  expect(queued.status).toBe(202);
}

describe("what the relay still holds after the windows lapse", () => {
  it("nothing, once the enrollment pickup poll has run the sweep", async () => {
    const { bundleId, ciphertext } = await leaveUncollectedBundle();
    await leaveUncollectedNote();

    // Sanity: it really is all held right now, so the absences below mean something.
    const before = db.dumpRows();
    expect(before).toContain(ciphertext);
    expect(before).toContain(NOTE_SENTINEL);
    expect(before).toContain("rate_limit:{");
    expect(before).not.toContain(PHONE_IP); // never, even before the sweep

    // A day later the Pi polls. That poll is the relay's only clock.
    clock.ms += DAY_MS;
    const poll = await pickup(device(DEVICE_TOKEN, "https://relay.test/api/pickup", "GET"), store);
    expect(poll.status).toBe(200);
    expect(((await poll.json()) as { items: unknown[] }).items).toEqual([]);

    const after = db.dumpRows();
    expect(after).not.toContain(ciphertext);
    expect(after).not.toContain(NOTE_SENTINEL);
    expect(after).not.toContain(PHONE_IP);
    // Not just the payloads — the rows themselves, including the status row the phone
    // was allowed to poll for an hour.
    for (const table of ["bundle", "invite", "rate_limit", "visitor_snapshot", "visitor_action"]) {
      expect(after).not.toContain(`${table}:{`);
    }
    expect(after).not.toContain(bundleId);
    expect(after).not.toContain(SESSION_ID);
  });

  it("nothing, once the visitor pickup poll has run it either", async () => {
    await leaveUncollectedNote();
    clock.ms += DAY_MS;
    const poll = await pickupVisitor(
      device(VISITOR_DEVICE_TOKEN, "https://relay.test/api/visitor/pickup", "GET"),
      store,
    );
    expect(poll.status).toBe(200);
    expect(db.dumpRows()).not.toContain(NOTE_SENTINEL);
  });

  it("keeps a bundle whose window has not lapsed", async () => {
    // The sweep must be a retention control, not an eager purge: inside the 15 minutes the
    // Pi can still collect.
    const { ciphertext } = await leaveUncollectedBundle();
    clock.ms += 10 * 60_000;
    const poll = await pickup(device(DEVICE_TOKEN, "https://relay.test/api/pickup", "GET"), store);
    expect(((await poll.json()) as { items: unknown[] }).items).toHaveLength(1);
    expect(db.dumpRows()).toContain(ciphertext);
  });
});
