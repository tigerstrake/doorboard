# integrations/home-assistant — HA bridge

Task: T-503 (Gemini, config-heavy) with control-plane wiring in T-501.

- Bridge between control-plane-api and Home Assistant on the NUC: MQTT discovery entities for doorboard state (bell events, presence labels, storage alerts) and inbound webhooks for phone Focus shortcuts / voluntary geofence labels feeding the presence engine.
- HA is an *integration surface*, never a dependency of the door path (ADR-0002): if HA is down, the door doesn't notice.
- Scoped long-lived token, NUC-only. The door Pi never talks to HA directly.
- Contents: MQTT topic map, HA config snippets/blueprints, webhook payload schemas (contracts-typed), setup doc.
