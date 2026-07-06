# infra/caddy

Caddy reverse-proxy config for the NUC if internal HTTPS is adopted (optional). Fronts control-plane-api and HA on the LAN. Nothing here exposes any service to the public internet — that is a hard security requirement (handoff §16).

## T-503 status (this task)

[`Caddyfile`](Caddyfile) routes `/ingest`, `/config/*`, `/people/*`,
`/admin/*`, `/health`, `/metrics`, and `/webhooks/*` to control-plane-api and
everything else to Home Assistant, both by Docker service name on the
compose network. Wired up as the `caddy` service in
[infra/compose/docker-compose.yml](../compose/docker-compose.yml), behind the
`https` profile — disabled by default, since most deployments stay on plain
HTTP within the LAN.

`{$DOORBOARD_INTERNAL_DOMAIN}` must be a LAN-only hostname (router/Pi-hole
DNS entry or an mDNS `.local` name), never a publicly routable domain.
Caddy's automatic HTTPS uses a locally-trusted internal CA for such names —
it never reaches out to a public ACME server for them — so turning this on
does not create any public exposure. Trust that internal CA on client
devices per [Caddy's own docs](https://caddyserver.com/docs/automatic-https#local-https)
(exact steps vary by OS/browser) if you want a padlock instead of a
certificate warning; skipping that step is harmless, just noisier.

Validated with `caddy validate --config Caddyfile --adapter caddyfile` in
development (no Docker available in that sandbox — see
`infra/compose/README.md` for the full list of what was and wasn't run
end-to-end); the reverse-proxy routing itself needs the real stack up to
verify.
