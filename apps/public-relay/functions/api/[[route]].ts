/**
 * The single Cloudflare Pages Function for the relay API (ADR-0043 §1). It catches every
 * `/api/*` request, resolves the D1-backed store from the binding, and hands off to the router
 * in `lib/apiRouter`. All the logic and routing is plain `lib` code the vitest suite exercises;
 * this file is the Cloudflare entry point plus the one thing that has to live at the edge of
 * the process: turning an uncaught throw into a JSON status and a single structured log line.
 *
 * The device-token check (lib/device) reads its secrets from `process.env`, which Cloudflare
 * populates from the project's vars/secrets under the `nodejs_compat` flag (ADR-0043 §1). The
 * D1 database is a binding, so it is passed in explicitly here rather than via env vars.
 */
import { routeApi, routeLabel } from "../../lib/apiRouter";
import { jsonError } from "../../lib/device";
import { resolveStore } from "../../lib/relayStore";
import type { D1Like } from "../../lib/d1/types";

interface Env {
  /** Absent only on a misconfigured deployment; every route then answers 503. */
  DB?: D1Like;
}

interface PagesFunctionContext {
  request: Request;
  env: Env;
}

export async function onRequest(context: PagesFunctionContext): Promise<Response> {
  const { request } = context;
  try {
    return await routeApi(request, resolveStore(context.env.DB));
  } catch (error) {
    // A malformed %-escape in a `:param` segment throws `URIError` from decodeURIComponent —
    // the caller's fault. Anything else (a D1 error, a bug) is ours. Either way the log line
    // names the route *pattern* and the error class only: no body, no token, no id.
    const status = error instanceof URIError ? 400 : 500;
    console.log(
      JSON.stringify({
        service: "public-relay",
        event: "request_failed",
        route: routeLabel(request),
        status,
        error: error instanceof Error ? error.name : typeof error,
      }),
    );
    return jsonError(status, status === 400 ? "bad_request" : "internal_error");
  }
}
