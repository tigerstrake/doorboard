# infra/caddy

Caddy reverse-proxy config for the NUC if internal HTTPS is adopted (optional). Fronts control-plane-api and HA on the LAN. Nothing here exposes any service to the public internet — that is a hard security requirement (handoff §16).
