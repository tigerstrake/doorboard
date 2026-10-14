#!/usr/bin/env bash
# Integration gate: typecheck → unit tests → production build → headless smoke runs.
# Usage: bash scripts/check.sh [--quick]   (--quick skips the smoke runs)
# Expects the dev server on http://localhost:5173 for smoke runs (npm run dev).
set -u
cd "$(dirname "$0")/.."

BASE="${BASE_URL:-http://localhost:5173}"
pass=(); fail=()
step() { # name, command...
  local name="$1"; shift
  echo -e "\n━━━ $name ━━━"
  if "$@"; then pass+=("$name"); else fail+=("$name"); fi
}

step "typecheck" npx tsc --noEmit -p .
step "unit tests" npx vitest run
step "build" npx vite build --logLevel warn

if [[ "${1:-}" != "--quick" ]]; then
  if curl -s -o /dev/null "$BASE/"; then
    step "smoke: 1P autopilot" node scripts/shot.mjs --url "$BASE/?input=keyboard&autostart=1&autopilot=1&seed=7" \
      --shots 3000,12000 --out shots/check/1p.png --eval "window.__bodySurfers && window.__bodySurfers.state()"
    step "smoke: 2P autopilot" node scripts/shot.mjs --url "$BASE/?input=keyboard&autostart=1&autopilot=1&players=2&seed=7" \
      --shots 4000 --out shots/check/2p.png --eval "window.__bodySurfers && window.__bodySurfers.state()"
    step "smoke: boot screen" node scripts/shot.mjs --url "$BASE/" --wait 3000 --out shots/check/boot.png
    step "smoke: camera path" node scripts/shot.mjs --url "$BASE/?autostart=1" --fake-camera --wait 7000 \
      --out shots/check/camera.png --eval "window.__bodySurfers && window.__bodySurfers.state()"
  else
    echo "dev server not reachable at $BASE — skipping smoke runs"; fail+=("smoke (no server)")
  fi
fi

echo -e "\n━━━ SUMMARY ━━━"
for p in "${pass[@]}"; do echo "  ✔ $p"; done
for f in "${fail[@]:-}"; do [[ -n "$f" ]] && echo "  ✘ $f"; done
[[ ${#fail[@]} -eq 0 ]]
