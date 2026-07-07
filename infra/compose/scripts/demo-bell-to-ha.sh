#!/usr/bin/env bash
# Scripted walkthrough of T-503's acceptance criterion: "Bell press in the
# simulated stack -> HA entity updates + a test notification fires
# end-to-end." Run after:
#
#   docker compose -f infra/compose/compose.dev.yml --env-file .env up -d
#
# This touches no application source: it drives `control-plane-api-admin`
# and `doorboard-simulator`'s existing CLIs (both already shipped by T-501/
# T-003) purely through their public interfaces, over the network, in
# containers that are already running.
set -euo pipefail

COMPOSE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/compose.dev.yml"
compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

echo "== 1. issuing an ingest-scoped token from control-plane-api =="
ISSUE_OUTPUT=$(compose exec -T control-plane-api control-plane-api-admin issue-token \
  --door-id primary --scope ingest --label demo-bell-to-ha)
# control_plane_api.cli prints `token_id=...` / `token=...` lines, not JSON
# (see apps/control-plane-api/src/control_plane_api/cli.py) — this demo
# talks to that CLI exactly as a human operator would, nothing added.
TOKEN=$(echo "$ISSUE_OUTPUT" | sed -n 's/^token=//p')
if [ -z "$TOKEN" ]; then
  echo "failed to issue a token; CLI output was:" >&2
  echo "$ISSUE_OUTPUT" >&2
  exit 1
fi
echo "token acquired"

echo
echo "== 2. running the basic-bell scenario inside the simulator container =="
echo "   (bell press -> door.button_pressed, media.recording_*, session.* events)"
BELL_EVENTS=$(compose exec -T simulator python3 -c '
import asyncio, json
from doorboard_simulator.scenarios import run_scenario_name

async def main():
    result = await run_scenario_name("basic-bell")
    events = [e.model_dump(mode="json") for e in result.events]
    print(json.dumps({"batch_id": "demo-basic-bell", "events": events}))

asyncio.run(main())
')

echo "== 3. POSTing the bell events to control-plane-api /ingest =="
compose exec -T control-plane-api python3 -c "
import json, sys, urllib.request
body = sys.stdin.read().encode()
req = urllib.request.Request(
    'http://127.0.0.1:8090/ingest', data=body, method='POST',
    headers={'Authorization': 'Bearer ${TOKEN}', 'Content-Type': 'application/json'},
)
with urllib.request.urlopen(req, timeout=10) as resp:
    print(resp.read().decode())
" <<<"$BELL_EVENTS"
echo
echo "   -> control-plane-api's MQTT audit fan-out just published"
echo "      doorboard/door/button_pressed (and friends) to Mosquitto."
echo "      Check Home Assistant (http://127.0.0.1:8123) for the 'Doorboard"
echo "      Bell' entity flipping on — it was discovered by"
echo "      ha-discovery-publisher at stack startup."

echo
echo "== 4. running the storage-low scenario (fires a critical storage_alert) =="
STORAGE_EVENTS=$(compose exec -T simulator python3 -c '
import asyncio, json
from doorboard_simulator.scenarios import run_scenario_name

async def main():
    result = await run_scenario_name("storage-low")
    events = [e.model_dump(mode="json") for e in result.events]
    print(json.dumps({"batch_id": "demo-storage-low", "events": events}))

asyncio.run(main())
')

echo "== 5. POSTing the storage-low events to control-plane-api /ingest =="
echo "   (control-plane-api's own notify engine — not HA — sends this;"
echo "    T-501 chose ntfy specifically so this path never depends on HA)"
compose exec -T control-plane-api python3 -c "
import json, sys, urllib.request
body = sys.stdin.read().encode()
req = urllib.request.Request(
    'http://127.0.0.1:8090/ingest', data=body, method='POST',
    headers={'Authorization': 'Bearer ${TOKEN}', 'Content-Type': 'application/json'},
)
with urllib.request.urlopen(req, timeout=10) as resp:
    print(resp.read().decode())
" <<<"$STORAGE_EVENTS"

echo
echo "== 6. checking the dev ntfy container for the notification =="
sleep 1
curl -sf "http://127.0.0.1:8880/doorboard-dev/json?poll=1" || true
echo
echo "Done. Open http://127.0.0.1:8123 (Home Assistant) and"
echo "http://127.0.0.1:8880/doorboard-dev (ntfy web view) to see both halves"
echo "of the acceptance criterion for real."
