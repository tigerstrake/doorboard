# infra/monitoring

Optional Prometheus + Grafana (or equivalent lightweight) stack on the NUC scraping every service's `/metrics`, including the door Pi over the LAN. Dashboards for the ARCHITECTURE.md §4 latency budgets, storage/queue health, and thermals are built in T-703. Alerting: storage low, sync queue aging, ESP32 offline, Pi thermal throttling.

## Configuration

`docker-compose.yml` reads these from the environment (or a `.env` file next to it — this
directory has no `.env.example` yet, so create one locally):

| Variable | Default | Notes |
|---|---|---|
| `GRAFANA_ADMIN_PASSWORD` | *(required, no default)* | Grafana is the only service in this stack with a login. The stack refuses to start without it — never leave it as `admin`. |
| `MONITORING_BIND_ADDR` | `127.0.0.1` | Applies to Prometheus, Alertmanager, and Grafana alike. Only widen this (e.g. to `0.0.0.0`) on a trusted LAN, and only if you understand the exposure: a Grafana admin can add datasources that reach postgres/mosquitto/home-assistant inside the compose network. |
| `CONTROL_PLANE_ADMIN_TOKEN` | `dev-admin-token` | Alertmanager's webhook auth; set to the same value configured on control-plane-api. |
