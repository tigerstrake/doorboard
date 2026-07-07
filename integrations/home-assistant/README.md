# integrations/home-assistant — HA bridge

Task: T-503 (Gemini, config-heavy) with control-plane wiring in T-501.

- Bridge between control-plane-api and Home Assistant on the NUC: MQTT discovery entities for doorboard state (bell events, presence labels, storage alerts) and inbound webhooks for phone Focus shortcuts / voluntary geofence labels feeding the presence engine.
- HA is an *integration surface*, never a dependency of the door path (ADR-0002): if HA is down, the door doesn't notice.
- Scoped long-lived token, NUC-only. The door Pi never talks to HA directly.
- Contents: MQTT topic map, HA config snippets/blueprints, webhook payload schemas (contracts-typed), setup doc.

## T-503 status (this task)

Runs as the `home-assistant` service in `infra/compose/` (T-503's compose
stack). Contents of this directory:

| Path | What it is |
|---|---|
| [`config/configuration.yaml`](config/configuration.yaml) | HA's MQTT broker connection (credentials via `!env_var`, sourced from `.env` through compose — no second secrets file to keep in sync) |
| [`config/rest_commands.yaml`](config/rest_commands.yaml) | `rest_command.presence_label_update` — the target the inbound webhook automation calls |
| [`config/automations.yaml`](config/automations.yaml) | The inbound webhook → control-plane-api bridge, disabled by default (see below) |
| [`discovery/`](discovery/) | Static MQTT Discovery config payloads for the four entities below, plus [`publish-discovery.sh`](discovery/publish-discovery.sh), run once at stack startup by the `ha-discovery-publisher` compose service |

These are bind-mounted read-only into the `home-assistant` container's
`/config`; HA's own runtime state (`.storage/`, its SQLite DB, logs) lives
in the separate `ha_data` named Docker volume and never touches this repo.

### MQTT topic map / discovery entities

| Entity | HA type | Topic (published by control-plane-api's audit fan-out) | Source event |
|---|---|---|---|
| Doorboard Bell | `binary_sensor` | `doorboard/door/button_pressed` | `door.button_pressed` |
| Doorboard Presence | `sensor` | `doorboard/status/presence_changed` | `status.presence_changed` |
| Doorboard Storage Alert | `sensor` | `doorboard/system/storage_alert` | `system.storage_alert` |
| Doorboard Sync Status | `sensor` | `doorboard/media/storage_status` | `media.storage_status` (`oldest_unsynced_s`) |

Full envelope/payload shapes: [docs/protocols/events.md](../../docs/protocols/events.md).
control-plane-api already publishes every ingested event to
`doorboard/<type-with-slashes>` (its MQTT audit fan-out, T-501,
`apps/control-plane-api/src/control_plane_api/mqtt.py`) — nothing here
changes that. `discovery/publish-discovery.sh` publishes the four **retained**
config messages above to `homeassistant/<component>/doorboard/<object_id>/config`
once; HA's MQTT integration auto-creates the entities as soon as it sees
them (discovery is automatic once the integration is configured — no
`discovery: true` flag needed in current HA versions).

Deliberately not surfaced: anything with a person's name (`vision.identity_stable.display_name`)
or exact location — out of scope for the four entities this brief lists,
and consistent with ARCHITECTURE.md §9 even though Mosquitto/HA are both
NUC-trusted (ARCHITECTURE.md §2).

### Inbound webhook: Focus shortcuts / geofence labels → control-plane-api

`config/automations.yaml` defines an HA `webhook` trigger (HA creates the
inbound endpoint automatically — no separate registration step) at:

```
http://<nuc-lan-host>:8123/api/webhook/doorboard-presence-label
```

A phone Shortcuts automation (iOS Focus change, or an Android
geofence/Tasker equivalent) POSTs `{"subject_id", "source", "label"}` to it
(broad labels only, per ARCHITECTURE.md §9); the automation forwards that to
`rest_command.presence_label_update`, which calls
`http://control-plane-api:8090/webhooks/presence`.

**That receiving endpoint does not exist yet** — presence computation is
T-504's scope (explicitly out of scope for T-503, see
[docs/tasks/T-503-nuc-stack.md](../../docs/tasks/T-503-nuc-stack.md)). The
automation ships with `initial_state: false` for exactly this reason: wiring
is ready, nothing calls it until T-504 lands and it's flipped to `true`. A
call against T-503-era control-plane-api 404s harmlessly — HA is never a
dependency of the door path (ADR-0002), so this can't regress anything.

### Setting up the HA-side long-lived token

If you extend this bridge to call the HA REST API directly (nothing here
does yet — the traffic today is HA *calling out* via `rest_command`, and
control-plane-api's own audit fan-out publishing *into* Mosquitto, which HA
subscribes to): create a long-lived access token from the HA user profile
page, scoped to a dedicated non-admin HA user if your HA version supports
per-user API tokens, and store it as `HOME_ASSISTANT_TOKEN` in `.env` (NUC
only — the door Pi never talks to HA directly). Never put it in `configuration.yaml`.

### Verifying without a NUC

`integrations/home-assistant/discovery/*.json` were validated with `python
-m json.tool`; `integrations/home-assistant/config/*.yaml` were checked for
correct HA schema shape by hand against HA's documented `mqtt:` /
`rest_command:` / `automation:` (`webhook` trigger) formats. Actually seeing
the four entities render and the webhook automation fire needs a running HA
instance — no Docker in this sandbox — see `infra/compose/README.md` and
`infra/compose/scripts/demo-bell-to-ha.sh` for how to exercise this for
real once Docker is available.
