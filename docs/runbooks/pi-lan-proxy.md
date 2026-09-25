# Runbook: the door Pi's LAN proxy (ADR-0047)

**Status:** Draft — needs on-device verification. Written against the repo (Caddyfile validated with `caddy validate`, path list pinned by `tests/test_pi_proxy_allowlist.py`); the unit has not yet run on the live Pi.
**Verified-on:** not yet — re-walk on the door Pi (home layout) and fill this in.

## What it is

Every door service binds `127.0.0.1`. One process listens on the LAN: `door-proxy.service`, which is Caddy running `deploy/pi-door/Caddyfile` (installed to `/etc/doorboard/Caddyfile`) on `0.0.0.0:80`. It forwards an explicit allow-list and answers **404 to everything else**:

| LAN path | Goes to | Why it is public |
|---|---|---|
| `/visitor`, `/visitor/*`, `/assets/*` | door-ui `127.0.0.1:5173` | the LAN-fallback visitor page (ADR-0017 §4, E-19) and the bundle it loads |
| `/visitor-session`, `/visitor-token`, `/guestbook`, `/polls/current`, `/polls/*/results`, `/polls/*/vote`, `/social/deletion-requests` | door-api `:8080` | the tokenised visitor surface — the same set the public relay carries (ADR-0017 §2) |
| `/admin/media-inbox/*/file`, `/admin/media-inbox/*/thumbnail` — only with an `Authorization: Bearer …` header, and only from `DOOR_PROXY_NUC_CIDR` if set | door-api `:8080` | the NUC's pull of a saved video message / ring photo (ADR-0012/0022); door-api checks the bearer |
| `/metrics/door-api`, `/metrics/door-visiond`, `/metrics/door-media`, `/metrics/door-sync`, `/metrics/door-voice`, `/metrics/node` | each service's loopback `/metrics` | Prometheus on the NUC scrapes the door through these ([monitoring-dashboards.md](monitoring-dashboards.md)) |

Not forwarded, by construction: `/admin/*` (other than the two media routes), `/ws`, `/session`, `/internal/*`, `/doorpad/*`, `/snapshot*`, `/recordings*`, `/@fs/*`. The owner's admin page is still opened at `http://127.0.0.1:5173/admin` on the Pi (or over an SSH tunnel); it is not a LAN surface any more.

**It is not on the door path.** The kiosks load `http://127.0.0.1:5173/...` and talk to door-api on loopback. If the proxy is dead the bell, the screens and recording are unaffected; what stops is the phone fallback QR and the NUC's media pull.

## Install

Both installers do this (`deploy/pi-door/install-systemd`, `install-home-layout`):

1. `apt install caddy` (Debian bookworm ships 2.6; the Caddyfile uses nothing newer).
2. The stock `caddy.service` the package enables is **disabled** — it would hold port 80 with the package's placeholder site.
3. `deploy/pi-door/Caddyfile` → `/etc/doorboard/Caddyfile`; `infra/systemd/door-proxy.service` enabled.
4. Optional: `/etc/doorboard/door-proxy.env` with `DOOR_PROXY_NUC_CIDR=<nuc-ip>/32` restricts the media routes to the NUC.

The proxy runs as a `DynamicUser` with `CAP_NET_BIND_SERVICE` only; it holds no token and no file but its config.

## Configuration that has to agree with it

- Pi `doorboard.env`: `DOOR_API_VISITOR_PUBLIC_BASE_URL=http://door-pi.local` — the LAN QR must name the proxy origin (port 80), not `:8080` or `:5173`.
- NUC `.env`: `CONTROL_PLANE_DOOR_API_BASE_URL=http://door-pi.local` — the media pull goes through the proxy.
- NUC `infra/monitoring/prometheus.yml`: the door jobs scrape `door-pi.local:80` with `metrics_path: /metrics/<service>`.

`tests/test_pi_proxy_allowlist.py` pins all three against the Caddyfile.

## Known gap — needs a door-ui change before the phone path works end to end

`apps/door-ui/src/apiBase.ts` derives door-api's origin as `${protocol}//${hostname}:8080`. A phone that loads `http://door-pi.local/visitor?token=…` through the proxy therefore calls `http://door-pi.local:8080/visitor-session…`, and nothing on the LAN listens on 8080 any more. The kiosk (loaded from `127.0.0.1:5173`) is unaffected. The fix is in door-ui, not here: when the page is served on the default port (no explicit `:5173`), use the page's own origin, so the visitor page calls `http://door-pi.local/visitor-session…` and the proxy forwards it. Until that lands, the LAN fallback page loads but its session lookup fails; the relay path is unaffected.

## Verify (on the Pi, then from a phone on the house wifi)

```bash
sudo systemctl status door-proxy
curl -s -o /dev/null -w '%{http_code}\n' http://door-pi.local/visitor            # 200 (index.html)
curl -s -o /dev/null -w '%{http_code}\n' http://door-pi.local/session            # 404
curl -s -o /dev/null -w '%{http_code}\n' http://door-pi.local/admin/recordings   # 404
curl -s -o /dev/null -w '%{http_code}\n' http://door-pi.local/ws                 # 404
curl -s -o /dev/null -w '%{http_code}\n' http://door-pi.local/admin/media-inbox/x/file                     # 404 (no bearer)
curl -s -o /dev/null -w '%{http_code}\n' -H 'Authorization: Bearer nope' http://door-pi.local/admin/media-inbox/x/file  # 401 or 404 FROM door-api, not the proxy
curl -s http://door-pi.local/metrics/door-api | head -3
```

Then ring with the relay blocked at the firewall: `GET /visitor-relay-status` on the Pi reports `qr_target: "lan"`, and the wallboard QR opens on a phone on the house wifi (see the gap above for the session call).

## Symptoms

- **Port 80 already in use** → the package's `caddy.service` is still enabled: `sudo systemctl disable --now caddy.service`, then `sudo systemctl restart door-proxy`.
- **Phone gets connection refused on `door-pi.local`** → `door-proxy` is down, or mDNS: `avahi-daemon` must be running on the Pi and the phone must be on the same L2 network.
- **NUC video/ring photos stopped arriving** → the NUC's `.env` still says `:8080`; or `DOOR_PROXY_NUC_CIDR` is set to an address the NUC no longer has (`journalctl -u door-proxy`).
- **Prometheus shows the door jobs down** → `curl http://door-pi.local/metrics/door-api` from the NUC host; if that works but the container cannot resolve `door-pi.local`, see the note in `infra/monitoring/README.md` (mDNS does not cross into Docker).

## Turning it off

`sudo systemctl disable --now door-proxy`. The door keeps working; the LAN QR and the NUC pulls stop.
