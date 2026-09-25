# ADR-0047: the door Pi has one LAN-facing proxy with an allow-list

**Status:** Accepted · **Date:** 2026-09-24 · **Relates to:** ADR-0002 (two planes), ADR-0017 §4 (LAN QR fallback, E-19), ADR-0012/0022 (NUC pulls media from door-api), ADR-0024 (admin reaches loopback services via door-api)

## Context

The deploy audit found that the door Pi's network surface is defined by accident, twice over:

- **The canonical layout** (`infra/systemd/*.service`, `deploy/pi-door/doorboard.env.example`) binds every
  door service to `127.0.0.1`. That is the trust model ARCHITECTURE §2 asks for, and it breaks three
  stated guarantees: the visitor QR's LAN fallback (`http://door.local/visitor?...` — nothing listens on
  port 80 in any layout, and the phone could not reach door-api on 8080 anyway), the NUC's pull of a saved
  video message and ring photo from door-api (`CONTROL_PLANE_DOOR_API_BASE_URL=http://door-pi.local:8080`),
  and Prometheus scraping the Pi's `/metrics`.
- **The live layout** (`deploy/pi-door/install-home-layout`) solves that by exposing the Vite dev server
  and door-api on `0.0.0.0`, which is the backlog's own [DEPLOY] finding: `/admin`, `/session`, `/ws`,
  camera snapshots and, through Vite's `/@fs/`, the whole checkout are readable from the dorm corridor.

Both are wrong, in opposite directions. The fix for one silently breaks the other, which is why the
backlog item has been re-picked and reverted. The missing piece is not a bind address; it is the
decision about *which paths* the LAN may reach at all.

## Decision

**1. Every door service binds loopback. Only one process listens on the LAN: a reverse proxy.**
door-api, door-media, door-visiond, door-sync, door-voice and door-ui bind `127.0.0.1`. A single
proxy unit (`door-proxy.service`, Caddy, config in `deploy/pi-door/Caddyfile`) listens on `0.0.0.0:80`
(and `:443` once a LAN CA exists) and forwards an explicit allow-list. Everything not listed is a 404
at the proxy, so `/admin`, `/ws`, `/session`, `/internal`, `/snapshot*`, `/recordings*` and the Vite
dev server (`/@fs/`, `/@vite/`) never leave the Pi.

**2. The allow-list is the public surface ADR-0017 already defined, plus two narrow NUC hops.**
- Visitor phone on the house wifi: door-ui's static bundle for `/visitor` (only), and door-api's
  `/visitor-session*` and the tokenised visitor read/write routes it needs — exactly the set the
  public relay carries (ADR-0017 §2), nothing more. `DOOR_API_VISITOR_PUBLIC_BASE_URL` becomes
  `http://door-pi.local` (the proxy origin), so E-19's fallback QR resolves in every layout.
- NUC → door-api media pull (ADR-0012/0022): the specific admin media-inbox routes control-plane-api
  calls, forwarded only with a valid `Authorization: Bearer` header present (the proxy does not verify
  it; door-api does), and only from the NUC's address when the operator sets one.
- NUC → Pi metrics: `/metrics/<service>` on the proxy maps to each loopback service's `/metrics`, so
  Prometheus scrapes `door-pi.local:80` and the monitoring stack finally observes the door.

**3. The proxy is not in the door path.** The kiosks keep talking to `127.0.0.1` directly; a dead
proxy costs the phone fallback and the NUC pulls, never the bell, the screens or recording. The unit
is `Wants=`-ed by nothing on the critical chain and starts after `network.target` only.

**4. The path list lives in one file and is tested.** `deploy/pi-door/Caddyfile` is the single
source; a test parses it and asserts (a) every forwarded path is on the public allow-list or the two
NUC hops, (b) no `/admin/*` other than the media-inbox routes, no `/ws`, `/session`, `/internal/*`,
`/snapshot*`, `/@fs/*` appears, and (c) `DOOR_API_VISITOR_PUBLIC_BASE_URL` in both env templates
points at an origin some unit in `infra/systemd` actually listens on.

## Consequences

- The [DEPLOY] backlog items "Vite on 0.0.0.0" and "LAN-exposed door-api" close by construction:
  `install-home-layout` installs `door-proxy.service`, serves the built bundle via `door-ui.service`
  on loopback, and stops passing `--host 0.0.0.0`.
- `/ws` and `/session` broadcasting recognised identity on the LAN (backlog §A) stops being reachable
  from the corridor; the socket-tiering ADR is still owed, but its blast radius is now loopback.
- The NUC's `CONTROL_PLANE_DOOR_API_BASE_URL` becomes `http://door-pi.local` (proxy), and
  `infra/monitoring/prometheus.yml` scrapes the Pi through it.
- TLS on the door plane (backlog §A) becomes a proxy-only change later: one listener, one certificate.
- Needs on-device verification: the Caddyfile is validated in CI (`caddy validate` where available,
  plus the path-list test) but the unit has not run on the live Pi in this change set.
