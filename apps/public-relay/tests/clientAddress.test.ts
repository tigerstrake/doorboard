/**
 * The rate-limit subject on Cloudflare.
 *
 * Cloudflare *appends* to a client-supplied `X-Forwarded-For` rather than replacing it, so its
 * leftmost entry is whatever the client chose — keyed on that, every per-IP limit was void and
 * each spoofed value became an unbounded row. `CF-Connecting-IP` is set by the edge from the
 * TCP peer. The subject is stored only as a truncated digest, so no address (real or claimed)
 * is ever at rest in the `rate_limit` table.
 */
import { beforeEach, describe, expect, it } from "vitest";

import { D1RelayStore } from "@/lib/d1/d1Store";
import { RATE_LIMITS, clientAddress, sha256Base64Url } from "@/lib/device";
import { handleGet as getInviteState } from "@/lib/handlers/enrollInvite";
import { resolveStore } from "@/lib/relayStore";

import { type NodeSqliteD1, freshD1 } from "./nodeSqliteD1";

const INVITE_ID = `inv_${"a".repeat(22)}`;

function request(headers: Record<string, string>): Request {
  return new Request(`https://relay.test/api/enroll/${INVITE_ID}`, { headers });
}

describe("clientAddress", () => {
  it("buckets by CF-Connecting-IP and ignores X-Forwarded-For", () => {
    const both = clientAddress(
      request({ "x-forwarded-for": "1.1.1.1", "cf-connecting-ip": "9.9.9.9" }),
    );
    expect(both).toBe(clientAddress(request({ "cf-connecting-ip": "9.9.9.9" })));
    expect(both).not.toBe(clientAddress(request({ "cf-connecting-ip": "1.1.1.1" })));
  });

  it("is a truncated digest, never the address itself", () => {
    const subject = clientAddress(request({ "cf-connecting-ip": "9.9.9.9" }));
    expect(subject).toBe(sha256Base64Url("9.9.9.9").slice(0, 22));
    expect(subject).not.toContain("9.9.9.9");
    expect(subject).toMatch(/^[A-Za-z0-9_-]{22}$/);
  });

  it("buckets everyone together when the header is missing — stricter, never looser", () => {
    const missing = clientAddress(request({}));
    expect(missing).toBe(sha256Base64Url("unknown").slice(0, 22));
    // A forged X-Forwarded-For does not buy a bucket of its own.
    expect(clientAddress(request({ "x-forwarded-for": "1.1.1.1" }))).toBe(missing);
  });
});

describe("per-IP limits on D1", () => {
  let db: NodeSqliteD1;

  beforeEach(async () => {
    db = await freshD1();
  });

  it("holds against a rotating X-Forwarded-For when CF-Connecting-IP is fixed", async () => {
    const statuses: number[] = [];
    for (let i = 0; i < 100; i += 1) {
      const resp = await getInviteState(
        request({ "x-forwarded-for": `10.0.${Math.floor(i / 256)}.${i % 256}`, "cf-connecting-ip": "9.9.9.9" }),
        resolveStore(db),
        { token: INVITE_ID },
      );
      statuses.push(resp.status);
    }
    expect(statuses.filter((status) => status === 429)).toHaveLength(
      100 - RATE_LIMITS.inviteLookup.limit,
    );

    // Nothing the relay wrote — not the row, not a bound parameter — carries an address.
    const written = db.dumpAll();
    expect(written).not.toContain("9.9.9.9");
    expect(written).not.toMatch(/10\.0\.\d+\.\d+/);
    // Sanity: the bucket row is really there, under the digest.
    expect(written).toContain(`invite:${clientAddress(request({ "cf-connecting-ip": "9.9.9.9" }))}`);
  });

  it("caps the row key however long the subject is", async () => {
    await new D1RelayStore(db).underRateLimit("probe", "x".repeat(8000), 10, 60);
    const row = await db
      .prepare("SELECT MAX(length(key)) AS n FROM rate_limit")
      .first<{ n: number }>();
    expect(row?.n).toBeLessThanOrEqual(96);
  });
});
