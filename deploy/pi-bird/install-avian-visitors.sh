#!/usr/bin/env bash
# Fresh-install AvianVisitors on the dedicated Doorboard bird node.

set -Eeuo pipefail
IFS=$'\n\t'

readonly AVIAN_REPOSITORY="https://github.com/Twarner491/AvianVisitors.git"
readonly AVIAN_COMMIT="1b33a3cbc4f3b1fe0f9987e2a381ef970283931f"
readonly INSTALL_DIR="${HOME}/BirdNET-Pi"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

validate_coordinate() {
  local name="$1"
  local value="$2"
  local minimum="$3"
  local maximum="$4"

  [[ "$value" =~ ^-?([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]] ||
    fail "${name} must be a decimal number"
  awk -v value="$value" -v minimum="$minimum" -v maximum="$maximum" \
    'BEGIN { exit !(value >= minimum && value <= maximum) }' ||
    fail "${name} must be between ${minimum} and ${maximum}"
}

set_config_value() {
  local key="$1"
  local value="$2"
  local config

  config="$(readlink -f /etc/birdnet/birdnet.conf)"
  [[ "$config" == "$INSTALL_DIR/birdnet.conf" ]] ||
    fail "unexpected upstream config target: ${config}"
  grep -q "^${key}=" "$config" || fail "upstream config is missing ${key}"
  sed -i "s|^${key}=.*$|${key}=${value}|" "$config"
}

[[ "${DOORBOARD_BIRD_NODE:-}" == "1" ]] ||
  fail "set DOORBOARD_BIRD_NODE=1 only on the dedicated bird Pi"
((EUID != 0)) || fail "run as the non-root bird-node user, not root"
[[ -n "${HOME:-}" && "$HOME" == /* ]] || fail "HOME must be an absolute path"

require_command awk
require_command dpkg
require_command getconf
require_command git
require_command grep
require_command readlink
require_command sed
require_command sudo
require_command systemctl
require_command uname
require_command visudo

sudo -n true >/dev/null 2>&1 || fail "passwordless sudo is required"

case "$(uname -m)" in
  aarch64 | x86_64) ;;
  *) fail "AvianVisitors requires aarch64 or x86_64" ;;
esac

[[ "$(getconf LONG_BIT)" == "64" ]] || fail "AvianVisitors requires a 64-bit OS"
case "$(dpkg --print-architecture)" in
  arm64 | amd64) ;;
  *) fail "Debian userspace must be arm64 or amd64" ;;
esac

# shellcheck disable=SC1091
source /etc/os-release
case "${ID:-}" in
  debian | raspbian) ;;
  *) fail "Raspberry Pi OS/Debian Bookworm or newer is required" ;;
esac
[[ "${VERSION_ID:-}" =~ ^[0-9]+([.][0-9]+)?$ ]] ||
  fail "cannot determine the Debian version from /etc/os-release"
awk -v version="$VERSION_ID" 'BEGIN { exit !(version >= 12) }' ||
  fail "Raspberry Pi OS/Debian Bookworm (12) or newer is required"

[[ -n "${AVIAN_VISITORS_LATITUDE:-}" ]] || fail "AVIAN_VISITORS_LATITUDE is required"
[[ -n "${AVIAN_VISITORS_LONGITUDE:-}" ]] || fail "AVIAN_VISITORS_LONGITUDE is required"
validate_coordinate "AVIAN_VISITORS_LATITUDE" "$AVIAN_VISITORS_LATITUDE" -90 90
validate_coordinate "AVIAN_VISITORS_LONGITUDE" "$AVIAN_VISITORS_LONGITUDE" -180 180

[[ ! -e "$INSTALL_DIR" && ! -L "$INSTALL_DIR" ]] ||
  fail "${INSTALL_DIR} already exists; this installer is for fresh nodes only"
[[ ! -e /etc/birdnet && ! -L /etc/birdnet ]] ||
  fail "/etc/birdnet already exists; use the documented upgrade or rollback procedure"

printf 'Fetching AvianVisitors commit %s into %s\n' "$AVIAN_COMMIT" "$INSTALL_DIR"
git init --quiet "$INSTALL_DIR"
git -C "$INSTALL_DIR" remote add origin "$AVIAN_REPOSITORY"
git -C "$INSTALL_DIR" fetch --depth 1 origin "$AVIAN_COMMIT"
git -C "$INSTALL_DIR" checkout --quiet --detach FETCH_HEAD

checked_out_commit="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
[[ "$checked_out_commit" == "$AVIAN_COMMIT" ]] ||
  fail "checked out ${checked_out_commit}, expected ${AVIAN_COMMIT}"

"$INSTALL_DIR/scripts/install_birdnet.sh"

# The upstream installer grants the PHP-FPM/Caddy user unrestricted root.
# AvianVisitors also installs its narrower command allowlist, which is enough
# for its status/config panels. Drop the unrestricted rule and disable local
# audio streaming and the browser terminal; Doorboard needs only recording,
# analysis, PHP-FPM, and the read-only recent-species API.
sudo test -f /etc/sudoers.d/020_avian-admin ||
  fail "upstream AvianVisitors sudo allowlist is missing"
sudo visudo -c -f /etc/sudoers.d/020_avian-admin >/dev/null
sudo rm -f /etc/sudoers.d/010_caddy-nopasswd
sudo visudo -c >/dev/null
sudo systemctl disable --now web_terminal.service livestream.service icecast2.service

# Upstream installs a weekly branch updater even when AUTOMATIC_UPDATE is off.
# Remove only that job; the bounded disk cleanup jobs remain in place.
sudo sed -i '\|update_birdnet[.]sh[[:space:]]\+-a|d' /etc/crontab
if grep -q 'update_birdnet[.]sh[[:space:]]\+-a' /etc/crontab; then
  fail "failed to remove the upstream automatic-update cron job"
fi
grep -q 'disk_check[.]sh' /etc/crontab || fail "upstream disk_check.sh cron job is missing"
grep -q 'disk_species_clean[.]sh' /etc/crontab ||
  fail "upstream disk_species_clean.sh cron job is missing"

set_config_value LATITUDE "$AVIAN_VISITORS_LATITUDE"
set_config_value LONGITUDE "$AVIAN_VISITORS_LONGITUDE"
set_config_value CONFIDENCE 0.70
set_config_value PRIVACY_THRESHOLD 1
set_config_value FULL_DISK purge
set_config_value PURGE_THRESHOLD 80
set_config_value MAX_FILES_SPECIES 25
set_config_value AUTOMATIC_UPDATE 0

checked_out_commit="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
[[ "$checked_out_commit" == "$AVIAN_COMMIT" ]] ||
  fail "installed checkout changed unexpectedly to ${checked_out_commit}"

printf '\nAvianVisitors is installed and pinned at %s.\n' "$AVIAN_COMMIT"
printf 'Reboot once when ready; this script deliberately does not reboot.\n'
printf 'After reboot run deploy/pi-bird/verify-avian-visitors.sh on the bird node.\n'
