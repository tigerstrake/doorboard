#!/bin/sh
# Generates the Mosquitto password file from environment variables at
# container start, then execs the broker. Never bakes credentials into the
# image or into git (handoff §16) — `acl.conf`/`mosquitto.conf` are static
# and secret-free; only this script touches the passwords, and it writes
# them to the `mosquitto_data` named volume, never back into the repo.
set -eu

PASSWD_FILE=/mosquitto/data/passwd
: > "$PASSWD_FILE"

require() {
  var_name="$1"
  eval "value=\${$var_name:-}"
  if [ -z "$value" ]; then
    echo "mosquitto entrypoint: $var_name is unset — refusing to start with a missing credential" >&2
    exit 1
  fi
}

for var in MQTT_CP_PASSWORD MQTT_PI_PASSWORD MQTT_HA_PASSWORD MQTT_HA_DISCOVERY_PASSWORD MQTT_HEALTHCHECK_PASSWORD; do
  require "$var"
done

mosquitto_passwd -b -c "$PASSWD_FILE" control-plane-api "$MQTT_CP_PASSWORD"
mosquitto_passwd -b "$PASSWD_FILE" door-pi "$MQTT_PI_PASSWORD"
mosquitto_passwd -b "$PASSWD_FILE" home-assistant "$MQTT_HA_PASSWORD"
mosquitto_passwd -b "$PASSWD_FILE" ha-discovery "$MQTT_HA_DISCOVERY_PASSWORD"
mosquitto_passwd -b "$PASSWD_FILE" healthcheck "$MQTT_HEALTHCHECK_PASSWORD"
chmod 600 "$PASSWD_FILE"

exec mosquitto -c /mosquitto/config/mosquitto.conf
