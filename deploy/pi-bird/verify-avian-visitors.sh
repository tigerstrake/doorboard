#!/usr/bin/env bash
# Verify the dedicated AvianVisitors bird node after its required reboot.

set -Eeuo pipefail
IFS=$'\n\t'

readonly AVIAN_COMMIT="1b33a3cbc4f3b1fe0f9987e2a381ef970283931f"
readonly INSTALL_DIR="${HOME}/BirdNET-Pi"
readonly RECENT_HOURS="${AVIAN_VISITORS_RECENT_HOURS:-24}"
readonly API_URL="${AVIAN_VISITORS_URL:-http://birdnet.local}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ "${DOORBOARD_BIRD_NODE:-}" == "1" ]] ||
  fail "set DOORBOARD_BIRD_NODE=1 only on the dedicated bird Pi"
((EUID != 0)) || fail "run as the non-root bird-node user, not root"
[[ "$RECENT_HOURS" =~ ^[0-9]+$ ]] || fail "AVIAN_VISITORS_RECENT_HOURS must be an integer"
((RECENT_HOURS >= 1 && RECENT_HOURS <= 168)) ||
  fail "AVIAN_VISITORS_RECENT_HOURS must be between 1 and 168"
if { [[ -n "${AVIAN_VISITORS_BASIC_USER:-}" ]] &&
  [[ -z "${AVIAN_VISITORS_BASIC_PASSWORD:-}" ]]; } ||
  { [[ -z "${AVIAN_VISITORS_BASIC_USER:-}" ]] &&
    [[ -n "${AVIAN_VISITORS_BASIC_PASSWORD:-}" ]]; }; then
  fail "set both AVIAN_VISITORS_BASIC_USER and AVIAN_VISITORS_BASIC_PASSWORD, or neither"
fi

for command_name in arecord awk curl git grep mktemp python3 sudo systemctl visudo; do
  command -v "$command_name" >/dev/null 2>&1 || fail "required command not found: $command_name"
done
sudo -n true >/dev/null 2>&1 || fail "passwordless sudo is required"

python3 - "$API_URL" <<'PY'
import sys
from urllib.parse import urlsplit

url = urlsplit(sys.argv[1])
if url.scheme not in {"http", "https"} or not url.hostname:
    raise SystemExit("AVIAN_VISITORS_URL must be an HTTP(S) base URL")
if url.username or url.password or url.query or url.fragment:
    raise SystemExit("AVIAN_VISITORS_URL must not contain credentials, a query, or a fragment")
PY

arecord -l 2>&1 | grep -q '^card [0-9]' || fail "no ALSA capture device found; check the USB mic"

for service_name in birdnet_recording.service birdnet_analysis.service caddy.service; do
  systemctl is-active --quiet "$service_name" || fail "${service_name} is not active"
done
for service_name in web_terminal.service livestream.service icecast2.service; do
  if systemctl is-active --quiet "$service_name"; then
    fail "${service_name} must stay disabled on the Doorboard bird node"
  fi
done
[[ ! -e /etc/sudoers.d/010_caddy-nopasswd ]] ||
  fail "upstream unrestricted Caddy sudo rule is present"
sudo test -f /etc/sudoers.d/020_avian-admin || fail "AvianVisitors sudo allowlist is missing"
sudo visudo -c -f /etc/sudoers.d/020_avian-admin >/dev/null ||
  fail "AvianVisitors sudo allowlist is invalid"
php_fpm_service="$(
  systemctl list-unit-files --type=service --no-legend 'php*-fpm.service' 2>/dev/null |
    awk 'NR == 1 { print $1 }'
)"
[[ -n "$php_fpm_service" ]] || fail "no PHP-FPM service is installed"
systemctl is-active --quiet "$php_fpm_service" || fail "${php_fpm_service} is not active"

[[ -d "$INSTALL_DIR/.git" ]] || fail "${INSTALL_DIR} is not a Git checkout"
installed_commit="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
[[ "$installed_commit" == "$AVIAN_COMMIT" ]] ||
  fail "installed commit is ${installed_commit}; expected ${AVIAN_COMMIT}"

[[ -r /etc/birdnet/birdnet.conf ]] || fail "/etc/birdnet/birdnet.conf is not readable"
for expected in \
  'CONFIDENCE=0.70' \
  'PRIVACY_THRESHOLD=1' \
  'FULL_DISK=purge' \
  'PURGE_THRESHOLD=80' \
  'MAX_FILES_SPECIES=25' \
  'AUTOMATIC_UPDATE=0'; do
  grep -qxF "$expected" /etc/birdnet/birdnet.conf || fail "missing retention setting: ${expected}"
done
grep -q 'disk_check[.]sh' /etc/crontab || fail "disk_check.sh cron job is missing"
grep -q 'disk_species_clean[.]sh' /etc/crontab || fail "disk_species_clean.sh cron job is missing"
if grep -q 'update_birdnet[.]sh[[:space:]]\+-a' /etc/crontab; then
  fail "automatic update cron is present; the deployment is not pinned"
fi

response_file="$(mktemp)"
trap 'rm -f "$response_file"' EXIT
curl_args=(
  --fail
  --silent
  --show-error
  --max-time 10
  --max-filesize 2000000
  --output "$response_file"
)
if [[ -n "${AVIAN_VISITORS_BASIC_USER:-}" ]]; then
  curl_args+=(--user "${AVIAN_VISITORS_BASIC_USER}:${AVIAN_VISITORS_BASIC_PASSWORD}")
fi
curl "${curl_args[@]}" \
  --url "${API_URL%/}/avian/api/birdnet-api.php?action=recent&hours=${RECENT_HOURS}"

python3 - "$response_file" "$RECENT_HOURS" <<'PY'
import json
import math
import sys
from datetime import datetime
from pathlib import Path

path = Path(sys.argv[1])
expected_hours = int(sys.argv[2])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid AvianVisitors JSON: {type(exc).__name__}") from exc

if not isinstance(payload, dict):
    raise SystemExit("AvianVisitors response must be an object")
if payload.get("hours") != expected_hours:
    raise SystemExit("AvianVisitors response hours do not match the request")
if not isinstance(payload.get("species"), list):
    raise SystemExit("AvianVisitors response species must be a list")
species = payload["species"]
if len(species) > 512:
    raise SystemExit("AvianVisitors response has too many species rows")
seen = set()
for row in species:
    if not isinstance(row, dict):
        raise SystemExit("AvianVisitors species row must be an object")
    sci = row.get("sci")
    common = row.get("com")
    count = row.get("n")
    confidence = row.get("best_conf")
    last_seen = row.get("last_seen")
    if not isinstance(sci, str) or not sci or len(sci) > 200:
        raise SystemExit("AvianVisitors species row has invalid sci")
    if not isinstance(common, str) or not common or len(common) > 200:
        raise SystemExit("AvianVisitors species row has invalid com")
    if type(count) is not int or not 1 <= count <= 1_000_000_000:
        raise SystemExit("AvianVisitors species row has invalid n")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        raise SystemExit("AvianVisitors species row has invalid best_conf")
    if not isinstance(last_seen, str):
        raise SystemExit("AvianVisitors species row has invalid last_seen")
    try:
        datetime.strptime(last_seen, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise SystemExit("AvianVisitors species row has invalid last_seen") from exc
    key = sci.casefold()
    if key in seen:
        raise SystemExit("AvianVisitors response contains duplicate species")
    seen.add(key)
as_of = payload.get("as_of")
if not isinstance(as_of, str):
    raise SystemExit("AvianVisitors response is missing as_of")
try:
    parsed_as_of = datetime.fromisoformat(as_of)
except ValueError as exc:
    raise SystemExit("AvianVisitors response as_of is not ISO-8601") from exc
if parsed_as_of.tzinfo is None:
    raise SystemExit("AvianVisitors response as_of must include a UTC offset")
PY

printf 'AvianVisitors verification passed at commit %s.\n' "$AVIAN_COMMIT"
