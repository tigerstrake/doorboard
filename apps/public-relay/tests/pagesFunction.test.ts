/**
 * The Cloudflare entry point (`functions/api/[[route]].ts`). Everything behind it is covered
 * by the handler suites; this pins the one job the Function has of its own — an uncaught
 * throw becomes a JSON status and exactly one structured log line that names the route
 * pattern and the error class, never a path, a body, or a token.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { D1Like } from "@/lib/d1/types";
import { onRequest } from "@/functions/api/[[route]]";

import { freshD1 } from "./nodeSqliteD1";

const VISITOR_TOKEN = "eyJ2IjoxfQ.c2lnbmF0dXJl";

let log: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  log = vi.spyOn(console, "log").mockImplementation(() => {});
});

afterEach(() => {
  log.mockRestore();
});

function logged(): Record<string, unknown> {
  expect(log).toHaveBeenCalledTimes(1);
  const line = log.mock.calls[0]![0];
  expect(typeof line).toBe("string");
  return JSON.parse(line as string) as Record<string, unknown>;
}

describe("onRequest", () => {
  it("serves a routed request untouched", async () => {
    const resp = await onRequest({
      request: new Request("https://relay.test/api/health"),
      env: { DB: await freshD1() },
    });
    expect(resp.status).toBe(200);
    expect(log).not.toHaveBeenCalled();
  });

  it("answers 503 storage_not_configured when the D1 binding is missing", async () => {
    // A misconfigured deployment should say so, not TypeError its way to a 500.
    const resp = await onRequest({
      request: new Request("https://relay.test/api/pickup", {
        headers: { authorization: "Bearer whatever" },
      }),
      env: {},
    });
    expect(resp.status).toBe(401); // auth is checked before storage, as on every device route
    const health = await onRequest({ request: new Request("https://relay.test/api/health"), env: {} });
    expect(await health.json()).toEqual({ service: "public-relay", status: "degraded" });
    const publicRoute = await onRequest({
      request: new Request("https://relay.test/api/door-key"),
      env: {},
    });
    expect(publicRoute.status).toBe(503);
    expect(await publicRoute.json()).toEqual({ error: "storage_not_configured" });
    expect(log).not.toHaveBeenCalled();
  });

  it("answers a malformed %-escape in a :param with 400, not a bare 500", async () => {
    const resp = await onRequest({
      request: new Request("https://relay.test/api/visitor/%E0%A4%A"),
      env: { DB: await freshD1() },
    });
    expect(resp.status).toBe(400);
    expect(await resp.json()).toEqual({ error: "bad_request" });
    expect(logged()).toEqual({
      service: "public-relay",
      event: "request_failed",
      route: "GET visitor/:token",
      status: 400,
      error: "URIError",
    });
  });

  it("answers a thrown store error with a JSON 500 and one log line naming the class only", async () => {
    const broken: D1Like = {
      prepare() {
        throw new Error(`D1_ERROR: too much data for one row; token was ${VISITOR_TOKEN}`);
      },
      async exec() {
        return null;
      },
    };
    const resp = await onRequest({
      request: new Request(`https://relay.test/api/visitor/${VISITOR_TOKEN}`),
      env: { DB: broken },
    });
    expect(resp.status).toBe(500);
    expect(await resp.json()).toEqual({ error: "internal_error" });

    const line = logged();
    expect(line).toMatchObject({ event: "request_failed", route: "GET visitor/:token", status: 500, error: "Error" });
    // The route pattern, not the path: the token is in the request line and must not reach a log.
    const raw = log.mock.calls[0]![0] as string;
    expect(raw).not.toContain(VISITOR_TOKEN);
    expect(raw).not.toContain("too much data");
  });
});
