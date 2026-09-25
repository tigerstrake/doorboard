/**
 * The D1 relay store (ADR-0043 §1), exercised against real SQLite via {@link NodeSqliteD1}.
 *
 * The point of these tests is the semantics KV could not give and D1 does: the single-use
 * invite, the pickup lease, the never-regressing terminal status, and the fixed-window rate
 * limiter — each a single guarded SQL statement here. P-33 (ADR-0043) is the lease + terminal
 * pair. A clock is injected so lease and TTL windows are deterministic.
 */
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import { beforeEach, describe, expect, it } from "vitest";

import type { SealedBundle } from "@/lib/contracts";
import { D1RelayStore } from "@/lib/d1/d1Store";
import { MIGRATIONS } from "@/lib/d1/schema";
import {
  BUNDLE_TTL_S,
  PICKUP_LEASE_S,
  STATUS_TTL_S,
  SWEEP_INTERVAL_S,
  type StoredVisitorSnapshot,
} from "@/lib/relayTypes";

import { NodeSqliteD1 } from "./nodeSqliteD1";

const clock = { ms: 1_000_000 };
let db: NodeSqliteD1;
let store: D1RelayStore;

beforeEach(async () => {
  clock.ms = 1_000_000;
  db = new NodeSqliteD1();
  store = new D1RelayStore(db, () => clock.ms);
  await store.migrate();
});

/** Distinctive enough to byte-scan for; the store never inspects ciphertext. */
const CIPHERTEXT_SENTINEL = "DOORBOARD_SENTINEL_CIPHERTEXT";

function sealed(bundleId: string, inviteId: string): SealedBundle {
  return {
    v: 1,
    suite: "ecies-p256-hkdf-sha256-aes256gcm",
    bundle_id: bundleId,
    invite_id: inviteId,
    door_key_id: "dky_x",
    ephemeral_public_key: "AA",
    salt: "BB",
    items: [{ index: 0, nonce: "CC", ciphertext: CIPHERTEXT_SENTINEL }],
  } as SealedBundle;
}

const HOUR_MS = 3_600_000;

function futureIso(msFromNow: number): string {
  return new Date(clock.ms + msFromNow).toISOString();
}

async function seedInvite(inviteId = "inv_a"): Promise<string> {
  await store.putInvite(inviteId, {
    secret_sha256: "hash",
    expires_at: futureIso(HOUR_MS),
    max_images: 5,
  });
  return inviteId;
}

describe("door key", () => {
  it("round-trips the active record and overwrites on re-publish", async () => {
    expect(await store.getDoorKey()).toBeNull();
    const rec = {
      door_key_id: "dky_1",
      suite: "s",
      public_key: "pk",
      fingerprint: "fp",
      consent_version: "v3",
      consent_text: "…",
      published_at: futureIso(0),
    };
    await store.putDoorKey(rec);
    expect((await store.getDoorKey())?.door_key_id).toBe("dky_1");
    await store.putDoorKey({ ...rec, door_key_id: "dky_2" });
    expect((await store.getDoorKey())?.door_key_id).toBe("dky_2");
  });
});

describe("invites", () => {
  it("stores, reads, and marks consumed", async () => {
    await seedInvite();
    const invite = await store.getInvite("inv_a");
    expect(invite?.consumed).toBe(false);
    expect(invite?.max_images).toBe(5);
    await store.markInviteConsumed("inv_a");
    expect((await store.getInvite("inv_a"))?.consumed).toBe(true);
  });

  it("re-registration never resurrects a consumed invite", async () => {
    await seedInvite();
    await store.markInviteConsumed("inv_a");
    // The Pi resyncs the same invite after an outage.
    await store.putInvite("inv_a", {
      secret_sha256: "hash",
      expires_at: futureIso(HOUR_MS),
      max_images: 5,
    });
    expect((await store.getInvite("inv_a"))?.consumed).toBe(true);
  });

  it("disappears once past its expiry-plus-grace", async () => {
    await seedInvite();
    clock.ms += HOUR_MS + 2 * HOUR_MS; // past expiry + the 1 h grace
    expect(await store.getInvite("inv_a")).toBeNull();
  });
});

describe("bundles and pickup lease", () => {
  it("goes pending on submit, then collected and returned on lease", async () => {
    await store.putBundle(sealed("bnd_1", "inv_a"));
    expect((await store.getStatus("bnd_1"))?.status).toBe("pending");

    const leased = await store.leaseBundles();
    expect(leased.map((b) => b.bundle.bundle_id)).toEqual(["bnd_1"]);
    expect((await store.getStatus("bnd_1"))?.status).toBe("collected");
  });

  it("never overwrites a bundle id it already holds", async () => {
    expect(await store.putBundle(sealed("bnd_1", "inv_a"))).not.toBeNull();
    await store.leaseBundles();
    await store.finishBundle("bnd_1", "enrolled", null);

    // A second submit under the same id — another invite, other ciphertext.
    const again = { ...sealed("bnd_1", "inv_b"), items: [{ index: 0, nonce: "CC", ciphertext: "ATTACKER" }] };
    expect(await store.putBundle(again as SealedBundle)).toBeNull();

    // The first bundle's record is untouched: still enrolled, still inv_a, nothing new stored.
    expect((await store.getStatus("bnd_1"))?.status).toBe("enrolled");
    const rows = db.dumpRows();
    expect(rows).not.toContain("ATTACKER");
    expect(rows).not.toContain("inv_b");
    expect(await store.leaseBundles()).toEqual([]);
  });

  it("P-33: does not hand the same bundle to a second poll inside the lease", async () => {
    await store.putBundle(sealed("bnd_1", "inv_a"));
    expect((await store.leaseBundles()).length).toBe(1);
    // A concurrent poll, still inside the lease, gets nothing.
    expect((await store.leaseBundles()).length).toBe(0);
    // Once the lease lapses (the Pi died mid-pickup), it is collectable again.
    clock.ms += PICKUP_LEASE_S * 1000 + 1;
    expect((await store.leaseBundles()).length).toBe(1);
  });

  it("finish deletes the ciphertext, returns the invite id, and stops re-collection", async () => {
    await store.putBundle(sealed("bnd_1", "inv_a"));
    await store.leaseBundles();
    const inviteId = await store.finishBundle("bnd_1", "enrolled", null);
    expect(inviteId).toBe("inv_a");
    expect((await store.getStatus("bnd_1"))?.status).toBe("enrolled");
    // No ciphertext left, and not collectable again even after the lease lapses.
    clock.ms += PICKUP_LEASE_S * 1000 + 1;
    expect(await store.leaseBundles()).toEqual([]);
  });

  it("P-33: a terminal status never regresses (a late duplicate ack cannot un-enroll)", async () => {
    await store.putBundle(sealed("bnd_1", "inv_a"));
    await store.finishBundle("bnd_1", "enrolled", null);
    await store.setStatus("bnd_1", "failed", "late duplicate");
    expect((await store.getStatus("bnd_1"))?.status).toBe("enrolled");
  });

  it("expires a bundle whose ciphertext TTL lapses before collection", async () => {
    await store.putBundle(sealed("bnd_1", "inv_a"));
    clock.ms += BUNDLE_TTL_S * 1000 + 1;
    expect(await store.leaseBundles()).toEqual([]);
    expect((await store.getStatus("bnd_1"))?.status).toBe("expired");
  });
});

describe("retention sweep", () => {
  it("builds the same schema from migrations/*.sql as from the code", () => {
    // The on-disk files are what `wrangler d1 execute` applies in production; the constants
    // are what the tests apply. Compared modulo comments and whitespace so neither can drift.
    const dir = path.join(__dirname, "..", "migrations");
    const files = readdirSync(dir).filter((name) => name.endsWith(".sql")).sort();
    expect(files).toHaveLength(MIGRATIONS.length);
    const normalise = (sql: string) => sql.replace(/--[^\n]*/g, "").replace(/\s+/g, " ").trim();
    files.forEach((file, index) => {
      expect(normalise(readFileSync(path.join(dir, file), "utf8"))).toBe(
        normalise(MIGRATIONS[index]!),
      );
    });
  });

  it("drops ciphertext at the bundle TTL even under a live lease, and the row at the status TTL", async () => {
    await store.putBundle(sealed("bnd_1", "inv_a"));
    // The Pi collected it and died: leased, never acked.
    expect((await store.leaseBundles()).length).toBe(1);
    expect(db.dumpRows()).toContain(CIPHERTEXT_SENTINEL);

    clock.ms += BUNDLE_TTL_S * 1000 + 1;
    await store.sweepExpired();
    expect(db.dumpRows()).not.toContain(CIPHERTEXT_SENTINEL);
    // The status outlives the ciphertext so the phone can be told what happened.
    expect((await store.getStatus("bnd_1"))?.status).toBe("expired");

    clock.ms += STATUS_TTL_S * 1000 + 1;
    await store.sweepExpired();
    expect(db.dumpRows()).not.toContain("bnd_1");
  });

  it("runs at most once per interval, from whichever poll claims the slot", async () => {
    expect(await store.sweepIfDue()).toBe(true);
    expect(await store.sweepIfDue()).toBe(false);
    clock.ms += SWEEP_INTERVAL_S * 1000 - 1;
    expect(await store.sweepIfDue()).toBe(false);
    clock.ms += 2;
    expect(await store.sweepIfDue()).toBe(true);
  });
});

describe("rate limiting", () => {
  it("counts within a fixed window and resets when it lapses", async () => {
    const under = async () => store.underRateLimit("submit", "ip", 3, 900);
    expect(await under()).toBe(true); // 1
    expect(await under()).toBe(true); // 2
    expect(await under()).toBe(true); // 3
    expect(await under()).toBe(false); // 4 — over
    clock.ms += 900 * 1000 + 1; // window lapses
    expect(await under()).toBe(true); // resets to 1
  });
});

describe("visitor surface", () => {
  function visitorSnapshot(overrides: Partial<StoredVisitorSnapshot> = {}): StoredVisitorSnapshot {
    return {
      session_token_sha256: "tokhash",
      session_id: "ses_1",
      state: "RINGING",
      expires_at: futureIso(300_000),
      poll: null,
      poll_results: null,
      outcomes: [],
      attributed: false,
      pushed_at: futureIso(0),
      ...overrides,
    };
  }

  it("finds a snapshot by token hash, and only with the matching hash", async () => {
    await store.putVisitorSnapshot(visitorSnapshot());
    expect((await store.getVisitorSnapshotByTokenHash("tokhash"))?.session_id).toBe("ses_1");
    expect(await store.getVisitorSnapshotByTokenHash("other")).toBeNull();
  });

  it("leases queued actions once and folds an outcome into the snapshot", async () => {
    await store.putVisitorSnapshot(visitorSnapshot());
    await store.queueVisitorAction("act_1", { action_id: "act_1", session_id: "ses_1", kind: "note" });
    expect(await store.pendingVisitorActionCount()).toBe(1);

    expect((await store.leaseVisitorActions()).length).toBe(1);
    expect((await store.leaseVisitorActions()).length).toBe(0); // leased, not handed twice

    await store.completeVisitorAction("ses_1", "act_1", { action_id: "act_1", entry_id: "g1" });
    const snap = await store.getVisitorSnapshotByTokenHash("tokhash");
    expect(snap?.outcomes).toEqual([{ action_id: "act_1", entry_id: "g1" }]);
  });

  it("folds an outcome once per action_id and keeps only the last 16", async () => {
    await store.putVisitorSnapshot(visitorSnapshot());
    for (let i = 0; i < 20; i += 1) {
      await store.completeVisitorAction("ses_1", `act_${i}`, { action_id: `act_${i}`, status: "applied" });
    }
    // A duplicate ack (door-api retried after a missed response) replaces, never appends.
    await store.completeVisitorAction("ses_1", "act_19", { action_id: "act_19", status: "rejected" });

    const outcomes = (await store.getVisitorSnapshotByTokenHash("tokhash"))?.outcomes as Array<{
      action_id: string;
      status: string;
    }>;
    expect(outcomes.map((o) => o.action_id)).toEqual(
      Array.from({ length: 16 }, (_unused, i) => `act_${i + 4}`),
    );
    expect(outcomes.at(-1)).toEqual({ action_id: "act_19", status: "rejected" });
  });

  it("folds nothing for a session it does not hold", async () => {
    await expect(
      store.completeVisitorAction("ses_missing", "act_1", { action_id: "act_1" }),
    ).resolves.toBeUndefined();
  });

  it("carries the attribution boolean, never a name (ADR-0044)", async () => {
    await store.putVisitorSnapshot(visitorSnapshot({ attributed: true }));
    const snap = await store.getVisitorSnapshotByTokenHash("tokhash");
    expect(snap?.attributed).toBe(true);
    expect(JSON.stringify(snap)).not.toContain("attributed_to");
  });
});
