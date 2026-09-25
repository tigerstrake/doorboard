/**
 * The Cloudflare entry point (`functions/api/[[route]].ts`). Everything behind it is covered
 * by the handler suites; this pins the one job the Function has of its own — an uncaught
 * throw becomes a JSON status and exactly one structured log line that names the route
 * pattern and the error class, never a path, a body, or a token.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { D1RelayStore } from "@/lib/d1/d1Store";
import type { D1Like } from "@/lib/d1/types";
import { onRequest } from "@/functions/api/[[route]]";

import { NodeSqliteD1 } from "./nodeSqliteD1";

const VISITOR_TOKEN = "eyJ2IjoxfQ.c2lnbmF0dXJl";

let log: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  log = vi.spyOn(console, "log").mockImplementation(() => {});
});

afterEach(() => {
  log.mockRestore();
});

async function migrated(): Promise<NodeSqliteD1> {
  const db = new NodeSqliteD1();
  await new D1RelayStore(db).migrate();
  return db;
}

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
      env: { DB: await migrated() },
    });
    expect(resp.status).toBe(200);
    expect(log).not.toHaveBeenCalled();
  });

  it("answers a malformed %-escape in a :param with 400, not a bare 500", async () => {
    const resp = await onRequest({
      request: new Request("https://relay.test/api/visitor/%E0%A4%A"),
      env: { DB: await migrated() },
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
