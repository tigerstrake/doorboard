# integrations/home-assistant — HA bridge

Task: T-503 (Gemini, config-heavy) with control-plane wiring in T-501.

- Bridge between control-plane-api and Home Assistant on the NUC: MQTT discovery entities for doorboard state (bell events, presence labels, storage alerts) and inbound webhooks for phone Focus shortcuts / voluntary geofence labels feeding the presence engine.
- HA is an *integration surface*, never a dependency of the door path (ADR-0002): if HA is down, the door doesn't notice.
- Scoped long-lived token, NUC-only. The door Pi never talks to HA directly.
- Contents: MQTT topic map, HA config snippets/blueprints, webhook payload schemas (contracts-typed), setup doc.

## What is here

Runs as the `home-assistant` service in `infra/compose/` (T-503's compose
stack). Contents of this directory:

| Path | What it is |
|---|---|
| [`config/configuration.yaml`](config/configuration.yaml) | `default_config`, the YAML dashboard below, a template sensor that turns the sync backlog into hours, and the `rest_command`/`automation` includes. The MQTT broker is deliberately **not** configured here — HA removed YAML broker config, so it is set up once in the UI (the file says how) and persists in `ha_data` |
| [`config/rest_commands.yaml`](config/rest_commands.yaml) | `rest_command.presence_focus_update` and `rest_command.presence_geofence_update` — one per control-plane-api presence source, both carrying the admin token from the container environment (`!env_var`, sourced from `.env` through compose — no second secrets file to keep in sync) |
| [`config/automations.yaml`](config/automations.yaml) | The two presence bridges (Focus webhook → `focus-shortcut`; companion-app zone change → `geofence-label`), both `initial_state: true`, plus phone pushes for the bell, a stalled archive and critical door storage |
| [`config/doorboard-dashboard.yaml`](config/doorboard-dashboard.yaml) | A YAML Lovelace dashboard for the four entities, in HA's sidebar as "Doorboard" |
| [`discovery/`](discovery/) | Static MQTT Discovery config payloads for the four entities below, plus [`publish-discovery.sh`](discovery/publish-discovery.sh), run once at stack startup by the `ha-discovery-publisher` compose service |

These are bind-mounted read-only, **per file**, into the `home-assistant`
container's `/config` (a new file needs its own mount or HA never sees it);
HA's own runtime state (`.storage/`, its SQLite DB, logs) lives in the
separate `ha_data` named Docker volume and never touches this repo. Because
`automations.yaml` is read-only, HA's UI automation editor cannot save —
automations for this stack are repo-managed on purpose.

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
http://<nuc-lan-host>:8123/api/webhook/<DOORBOARD_PRESENCE_WEBHOOK_ID>
```

A phone Shortcuts automation (iOS Focus change, or an Android
geofence/Tasker equivalent) POSTs `{"subject_id", "label"}` to it (broad
labels only, per ARCHITECTURE.md §9; no `source` — the endpoint fixes it);
the automation forwards that to `rest_command.presence_focus_update`, which
calls `http://control-plane-api:8090/status/presence/webhook/focus-shortcut`
with the admin token. A second automation reacts to the companion app's zone
changes and calls `presence_geofence_update` the same way. Both ship
`initial_state: true` and are pinned so by `tests/test_home_assistant_presence_config.py`.

**The webhook id is the credential.** HA authenticates an inbound webhook by
nothing but the secrecy of its id, and everything that reaches the automation
is forwarded with admin authority as a `focus_shortcut` update — which outranks
geofence and calendar (ADR-0035) and is shown on the public wallboard. The
`source` cannot be spoofed (it comes from which URL is called), but the
*caller* can be by anyone who knows the id, and `local_only: true` only narrows
that to the LAN. So the id is `DOORBOARD_PRESENCE_WEBHOOK_ID` in `.env`,
delivered through the container environment via `!env_var` like the admin
token, generated per deployment (`openssl rand -hex 32`), and never a literal
in this repo. Rotation: [docs/runbooks/security-checklist.md](../../docs/runbooks/security-checklist.md).

### Which person, which zones

The zone automation triggers on one `person.*` entity and maps zone names to
labels. Both are per-deployment facts, not repo facts: HA derives the entity
id from the name in Settings → People (and a wrong id fails silently, by
never triggering), and zone names are whatever you called them. So the entity
comes from `DOORBOARD_PRESENCE_PERSON_ENTITY` in `.env`, through the container
environment like the webhook id — compose defaults it to the placeholder
`person.owner` — and the mapping in `automations.yaml` ships with generic zone
names (`home`, `Campus`, `Library`) to rename to match yours. Anything
unmapped, another city or another country included, resolves to `away` on
purpose: the label never says where you are, only that you are not here.

### Setting up the HA-side long-lived token

If you extend this bridge to call the HA REST API directly (nothing here
does yet — the traffic today is HA *calling out* via `rest_command`, and
control-plane-api's own audit fan-out publishing *into* Mosquitto, which HA
subscribes to): create a long-lived access token from the HA user profile
page, scoped to a dedicated non-admin HA user if your HA version supports
per-user API tokens, and store it as `HOME_ASSISTANT_TOKEN` in `.env` (NUC
only — the door Pi never talks to HA directly). Never put it in `configuration.yaml`.

### Verifying without a NUC

`tests/test_home_assistant_presence_config.py` loads the YAML and checks it
against the code it calls: every `rest_command` targets a route
control-plane-api actually serves, sends no `source` and no coordinates,
and authenticates from the environment; both presence automations are
explicitly enabled; every label the zone template can render is one the
contract accepts; and the webhook id and person entity reach the container
from `.env` with only placeholders in git. It exists because an earlier
version of this config was "checked by hand against HA's documented
formats" and had three independently fatal faults that nothing caught until
a Focus change did nothing. Actually seeing the four entities render and the
webhook automation fire still needs a running HA — see
`infra/compose/README.md` and `infra/compose/scripts/demo-bell-to-ha.sh`.
