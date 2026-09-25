# infra/monitoring

Optional Prometheus + Alertmanager + Grafana stack on the NUC. It scrapes control-plane-api on the compose network and the door Pi **through its LAN proxy** (ADR-0047): every door service binds loopback, and `door-proxy.service` maps `/metrics/<service>` onto each one, so all door jobs target `door-pi.local:80` and differ only in `metrics_path`. The dashboard (`grafana/dashboards/doorboard.json`) covers the ARCHITECTURE.md §4 vision latencies, storage/queue health, the ESP32 link and Pi thermals; the rules in `alert.rules.yml` are listed in [docs/runbooks/monitoring-dashboards.md](../../docs/runbooks/monitoring-dashboards.md).

`tests/test_monitoring_rules.py` keeps this honest: every metric name in the rules and the dashboard must be emitted by some exporter under `apps/` (or come from a scrape job that exists), and every target port must be one the service binds.

**Needs on-device verification:** the Pi scrape path and the node-exporter thermal label were checked against the repo and the package, not against the live door.

## Running it

```bash
cd infra/monitoring
GRAFANA_ADMIN_PASSWORD=... CONTROL_PLANE_ADMIN_TOKEN=... docker compose up -d
```

It joins the NUC stack's network (`doorboard-nuc_default`) so `control-plane-api:8090` and `alertmanager:9093` resolve by service name.

### `door-pi.local` must resolve inside the container

mDNS does not cross into Docker: the NUC host may resolve `door-pi.local` through avahi while the Prometheus container cannot. Either give the Pi a DHCP reservation and a name in the router's DNS, or add to `docker-compose.yml`:

```yaml
    extra_hosts:
      - "door-pi.local:192.168.x.y"
```

`docker compose exec prometheus wget -qO- http://door-pi.local/metrics/door-api | head` is the check.

### On the Pi

- `door-proxy.service` must be running (both installers enable it).
- `apt install prometheus-node-exporter` for `PiThrottling` and the thermal panel. The package's own `prometheus-node-exporter.service` listens on `0.0.0.0:9100`, which ADR-0047 forbids; the installers disable it and enable `infra/systemd/node-exporter.service`, which runs the same binary on `127.0.0.1:9100` behind `/metrics/node`. Without the package the unit is skipped (`ConditionPathExists`) and only the thermal rule/panel stay empty.

## Configuration

`docker-compose.yml` reads these from the environment (or a `.env` file next to it — this directory has no `.env.example`, so create one locally):

| Variable | Default | Notes |
|---|---|---|
| `GRAFANA_ADMIN_PASSWORD` | *(required)* | Grafana is the only service in this stack with a login. The stack refuses to start without it — never leave it as `admin`. |
| `MONITORING_BIND_ADDR` | `127.0.0.1` | Applies to Prometheus, Alertmanager, and Grafana alike. Only widen this (e.g. to `0.0.0.0`) on a trusted LAN, and only if you understand the exposure: a Grafana admin can add datasources that reach postgres/mosquitto/home-assistant inside the compose network. |
| `CONTROL_PLANE_ADMIN_TOKEN` | *(required)* | Alertmanager's webhook auth for `POST /admin/alerts` on control-plane-api; the same value the NUC `.env` sets. No dev default: this is the NUC admin secret, and an Alertmanager posting `dev-admin-token` at a production control plane fails silently. |

## Where an alert goes, and the self-check

Alertmanager has one receiver: `POST http://control-plane-api:8090/admin/alerts` with the admin bearer, which the control plane fans out to the owner through its notifier (ntfy/Telegram). That means the alert path has exactly one hop that can fail invisibly — the receiver itself. If control-plane-api is down or rejects the token, every alert (including `ServiceDown` for control-plane-api) goes nowhere.

The `AlertmanagerReceiverFailing` rule (`increase(alertmanager_notifications_failed_total[10m]) > 0`, from the `alertmanager` scrape job) is the self-check, and by construction it **cannot page you through the receiver it is reporting on**. Read it where it lives: the Prometheus alerts page (`http://<nuc>:9090/alerts`) or the Alertmanager UI (`:9093`). Add it to your weekly glance at the dashboard; it is the one alert that has to be pulled, not pushed.

## Backups

There is no backup-age metric (the `postgres-backup` service is a shell loop, not an exporter), so the backup-stale check is a compose healthcheck on that service: it is `unhealthy` when no `doorboard-*.sql.gz` under `NAS_BACKUP_PATH` is younger than two `BACKUP_INTERVAL_S`. `docker compose ps` shows it; wire `docker events --filter event=health_status` into a notification if you want a push. See [docs/runbooks/backup-procedures.md](../../docs/runbooks/backup-procedures.md).
