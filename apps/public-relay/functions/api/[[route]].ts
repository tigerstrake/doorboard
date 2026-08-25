/**
 * The single Cloudflare Pages Function for the relay API (ADR-0043 §1). It catches every
 * `/api/*` request, resolves the D1-backed store from the binding, and hands off to the router
 * in `lib/apiRouter`. All the logic and routing is plain `lib` code the vitest suite exercises;
 * this file is only the Cloudflare entry point, so it needs no test of its own.
 *
 * The device-token check (lib/device) reads its secrets from `process.env`, which Cloudflare
 * populates from the project's vars/secrets under the `nodejs_compat` flag (ADR-0043 §1). The
 * D1 database is a binding, so it is passed in explicitly here rather than via env vars.
 */
import { routeApi } from "../../lib/apiRouter";
import { resolveStore } from "../../lib/relayStore";
import type { D1Like } from "../../lib/d1/types";

interface Env {
  DB: D1Like;
}

interface PagesFunctionContext {
  request: Request;
  env: Env;
}

export function onRequest(context: PagesFunctionContext): Promise<Response> {
  return routeApi(context.request, resolveStore(context.env.DB));
}
