/**
 * The Cloudflare API router (ADR-0043 §1). The handlers themselves are covered by relay.test /
 * visitor.test; this pins the routing table the catch-all Function relies on — method + path
 * dispatch, `:param` capture, and the ordering that makes literal segments win over `:token`
 * (so `/api/visitor/pickup` is the pickup route, not a visitor snapshot for token "pickup").
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { routeApi } from "@/lib/apiRouter";
import { resolveStore } from "@/lib/relayStore";
import { setRedisForTests } from "@/lib/store";

import { FakeRedis } from "./fakeRedis";

beforeEach(() => {
  setRedisForTests(new FakeRedis());
  process.env.UPSTASH_REDIS_REST_URL = "https://fake.upstash.test";
  process.env.UPSTASH_REDIS_REST_TOKEN = "fake-token";
});

afterEach(() => setRedisForTests(null));

function req(path: string, method = "GET"): Request {
  return new Request(`https://relay.test${path}`, {
    method,
    headers: { "content-type": "application/json", "x-forwarded-for": "198.51.100.9" },
  });
}

const store = () => resolveStore();

describe("routeApi", () => {
  it("dispatches a known GET route", async () => {
    const resp = await routeApi(req("/api/health"), store());
    expect(resp.status).toBe(200);
  });

  it("404s an unknown path", async () => {
    const resp = await routeApi(req("/api/nope"), store());
    expect(resp.status).toBe(404);
  });

  it("405s a known path with the wrong method", async () => {
    const resp = await routeApi(req("/api/health", "DELETE"), store());
    expect(resp.status).toBe(405);
  });

  it("captures a :param and routes it (status/:bundleId)", async () => {
    // Unknown bundle → the status handler synthesises `expired` (public, no auth). A 200 here
    // means the `:bundleId` route matched and the id was passed through.
    const resp = await routeApi(req("/api/status/bnd_does_not_exist"), store());
    expect(resp.status).toBe(200);
    expect(((await resp.json()) as { status: string }).status).toBe("expired");
  });

  it("captures :token on the public enroll route", async () => {
    const resp = await routeApi(req(`/api/enroll/inv_${"a".repeat(22)}`), store());
    // No secret header → the enroll handler returns `unknown`, 200. Proves the route matched.
    expect(resp.status).toBe(200);
    expect(((await resp.json()) as { status: string }).status).toBe("unknown");
  });

  it("prefers the literal `visitor/pickup` over `visitor/:token`", async () => {
    // Routed to the device-gated pickup handler → 401 (token required). Had it matched
    // `visitor/:token` with token "pickup", it would be a 404 session_not_found instead.
    const resp = await routeApi(req("/api/visitor/pickup"), store());
    expect(resp.status).toBe(401);
  });

  it("prefers the literal `visitor/session` over `visitor/:token`", async () => {
    const resp = await routeApi(req("/api/visitor/session", "PUT"), store());
    expect(resp.status).toBe(401); // device-gated session push, not a snapshot lookup
  });
});
