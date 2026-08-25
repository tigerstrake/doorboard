/**
 * The D1 relay store (ADR-0043 §1), exercised against real SQLite via {@link NodeSqliteD1}.
 *
 * The point of these tests is the semantics KV could not give and D1 does: the single-use
 * invite, the pickup lease, the never-regressing terminal status, and the fixed-window rate
 * limiter — each a single guarded SQL statement here. P-33 (ADR-0043) is the lease + terminal
 * pair. A clock is injected so lease and TTL windows are deterministic.
 */
import { beforeEach, describe, expect, it } from "vitest";

import type { SealedBundle } from "@/lib/contracts";
import { D1RelayStore } from "@/lib/d1/d1Store";
import {
  BUNDLE_TTL_S,
  PICKUP_LEASE_S,
  type StoredVisitorSnapshot,
} from "@/lib/store";

import { NodeSqliteD1 } from "./nodeSqliteD1";

const clock = { ms: 1_000_000 };
let store: D1RelayStore;

beforeEach(async () => {
  clock.ms = 1_000_000;
  store = new D1RelayStore(new NodeSqliteD1(), () => clock.ms);
  await store.migrate();
});

function sealed(bundleId: string, inviteId: string): SealedBundle {
  return {
    v: 1,
    suite: "ecies-p256-hkdf-sha256-aes256gcm",
    bundle_id: bundleId,
    invite_id: inviteId,
    door_key_id: "dky_x",
    ephemeral_public_key: "AA",
    salt: "BB",
    items: [{ index: 0, nonce: "CC", ciphertext: "DD" }],
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

  it("carries the attribution boolean, never a name (ADR-0044)", async () => {
    await store.putVisitorSnapshot(visitorSnapshot({ attributed: true }));
    const snap = await store.getVisitorSnapshotByTokenHash("tokhash");
    expect(snap?.attributed).toBe(true);
    expect(JSON.stringify(snap)).not.toContain("attributed_to");
  });
});
