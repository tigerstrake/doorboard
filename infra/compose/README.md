# infra/compose

Docker Compose files for the NUC stack (HA, Mosquitto, PostgreSQL, control-plane-api, wallboard-worker, Caddy, monitoring) and a `compose.dev.yml` that runs the full simulated system (simulator + door services in mock mode + control plane) on a laptop. Authored in T-503/T-000. Secrets via env files only.
