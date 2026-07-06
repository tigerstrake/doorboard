#!/bin/sh
# One-shot: publishes retained HA MQTT Discovery config messages (T-503's
# "MQTT discovery entities: bell event, presence, storage alert, sync
# status"). Runs as the `ha-discovery-publisher` compose service
# (image: eclipse-mosquitto:2, which bundles mosquitto_pub — no separate
# Python/Dockerfile needed) and exits; retained messages mean HA picks the
# entities up on its next MQTT connect even if this runs before HA does.
#
# Deliberately does not touch control-plane-api: it publishes using the
# `ha-discovery` Mosquitto credential (write-only to `homeassistant/#`,
# infra/compose/mosquitto/acl.conf), reading the same static JSON payloads
# this directory ships — no service code anywhere else changes.
set -eu

: "${MQTT_HOST:=mosquitto}"
: "${MQTT_HA_DISCOVERY_PASSWORD:?MQTT_HA_DISCOVERY_PASSWORD must be set}"

MANIFEST="$(dirname "$0")/manifest.txt"

while IFS='|' read -r topic file; do
  case "$topic" in
    \#*|"") continue ;;  # comments / blank lines
  esac
  path="$(dirname "$0")/$file"
  echo "publishing discovery config: $topic <- $file"
  mosquitto_pub -h "$MQTT_HOST" -p 1883 \
    -u ha-discovery -P "$MQTT_HA_DISCOVERY_PASSWORD" \
    -t "$topic" -f "$path" -r -q 1
done < "$MANIFEST"

echo "discovery publish complete"
