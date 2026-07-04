# infra/monitoring

Optional Prometheus + Grafana (or equivalent lightweight) stack on the NUC scraping every service's `/metrics`, including the door Pi over the LAN. Dashboards for the ARCHITECTURE.md §4 latency budgets, storage/queue health, and thermals are built in T-703. Alerting: storage low, sync queue aging, ESP32 offline, Pi thermal throttling.
