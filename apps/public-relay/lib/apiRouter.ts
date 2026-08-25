/**
 * The API router (ADR-0043 §1). On Cloudflare the single catch-all Pages Function
 * (`functions/api/[[route]].ts`) hands every `/api/*` request here with a store already
 * resolved from the D1 binding. Keeping the routing table in `lib` (not in the Function) means
 * it is plain code the vitest suite can exercise, and the Function stays a two-line wrapper.
 *
 * Routing is explicit and ordered: literal-segment routes are listed before their
 * `:param` siblings (so `visitor/session` and `visitor/pickup` win over `visitor/:token`).
 */
import { jsonError } from "./device";
import * as doorKey from "./handlers/doorKey";
import * as enrollInvite from "./handlers/enrollInvite";
import * as enrollSubmit from "./handlers/enrollSubmit";
import * as health from "./handlers/health";
import * as invite from "./handlers/invite";
import * as inviteDelete from "./handlers/inviteDelete";
import * as pickup from "./handlers/pickup";
import * as pickupAck from "./handlers/pickupAck";
import * as status from "./handlers/status";
import * as visitorAction from "./handlers/visitorAction";
import * as visitorPickup from "./handlers/visitorPickup";
import * as visitorPickupAck from "./handlers/visitorPickupAck";
import * as visitorSession from "./handlers/visitorSession";
import * as visitorSnapshot from "./handlers/visitorSnapshot";
import type { RelayStore } from "./relayTypes";

type Handler = (
  request: Request,
  store: RelayStore,
  params: Record<string, string>,
) => Promise<Response>;

interface Route {
  method: string;
  /** Path under `/api/`, split into segments; a `:name` segment captures a param. */
  segments: string[];
  handler: Handler;
}

const ROUTES: Route[] = [
  { method: "GET", segments: ["door-key"], handler: doorKey.handleGet },
  { method: "PUT", segments: ["door-key"], handler: doorKey.handlePut },
  { method: "PUT", segments: ["invite"], handler: invite.handlePut },
  { method: "DELETE", segments: ["invite", ":inviteId"], handler: inviteDelete.handleDelete },
  { method: "GET", segments: ["enroll", ":token"], handler: enrollInvite.handleGet },
  { method: "POST", segments: ["enroll", ":token", "submit"], handler: enrollSubmit.handlePost },
  { method: "GET", segments: ["pickup"], handler: pickup.handleGet },
  { method: "POST", segments: ["pickup", "ack"], handler: pickupAck.handlePost },
  { method: "GET", segments: ["status", ":bundleId"], handler: status.handleGet },
  { method: "GET", segments: ["health"], handler: health.handleGet },
  { method: "PUT", segments: ["visitor", "session"], handler: visitorSession.handlePut },
  { method: "GET", segments: ["visitor", "pickup"], handler: visitorPickup.handleGet },
  { method: "POST", segments: ["visitor", "pickup", "ack"], handler: visitorPickupAck.handlePost },
  // param routes last, so the literal `session`/`pickup` above win.
  { method: "GET", segments: ["visitor", ":token"], handler: visitorSnapshot.handleGet },
  { method: "POST", segments: ["visitor", ":token", "action"], handler: visitorAction.handlePost },
];

/** Match a route pattern against actual segments; return captured params or null. */
function match(pattern: string[], actual: string[]): Record<string, string> | null {
  if (pattern.length !== actual.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < pattern.length; i += 1) {
    const p = pattern[i]!;
    const a = actual[i]!;
    if (p.startsWith(":")) params[p.slice(1)] = decodeURIComponent(a);
    else if (p !== a) return null;
  }
  return params;
}

/** Route one `/api/*` request to its handler with the given store. */
export async function routeApi(request: Request, store: RelayStore): Promise<Response> {
  const { pathname } = new URL(request.url);
  const rel = pathname.replace(/^\/+api\/*/, "").replace(/\/+$/, "");
  const segments = rel.length ? rel.split("/") : [];

  let pathMatchedWrongMethod = false;
  for (const route of ROUTES) {
    const params = match(route.segments, segments);
    if (!params) continue;
    if (route.method !== request.method) {
      pathMatchedWrongMethod = true;
      continue;
    }
    return route.handler(request, store, params);
  }
  return jsonError(pathMatchedWrongMethod ? 405 : 404, pathMatchedWrongMethod ? "method_not_allowed" : "not_found");
}
