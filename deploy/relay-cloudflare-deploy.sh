#!/usr/bin/env bash
#
# Turnkey deploy of the relay to Cloudflare Pages (ADR-0043, door.tigerstrake.com).
# Companion to docs/runbooks/relay-cloudflare-deploy.md — this automates every step that
# doesn't require you personally. You do exactly three interactive things:
#   1. `npx wrangler login` once, before running this (a browser OAuth — a script can't do it).
#   2. paste the two device-token values when this script prompts (wrangler reads them
#      directly; this script and the assistant never see them).
#   3. add the custom domain in the dashboard at the end (two clicks; printed below).
#
# No global install needed — wrangler runs via npx (no sudo). Everything else — create the D1
# database, wire its id into wrangler.toml, apply the schema, build the static site, and deploy —
# is done for you. Safe to re-run: every step is idempotent.
#
# Usage:  npx wrangler login                       # once (no install, no sudo)
#         ./deploy/relay-cloudflare-deploy.sh
set -euo pipefail

DB_NAME="doorboard-relay"
PROJECT="doorboard-relay"
DOMAIN="door.tigerstrake.com"

# Run from the relay app dir (where wrangler.toml + next.config live).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELAY_DIR="$SCRIPT_DIR/../apps/public-relay"
cd "$RELAY_DIR"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$1" >&2; exit 1; }

# Resolve wrangler without a global (sudo) install: PATH, else the app's local copy, else npx
# (downloads on demand, cached). So `npx wrangler login` is all you need up front.
if command -v wrangler >/dev/null 2>&1; then WRANGLER=(wrangler)
elif [ -x node_modules/.bin/wrangler ]; then WRANGLER=(node_modules/.bin/wrangler)
else WRANGLER=(npx --yes wrangler); fi
echo "Using wrangler: ${WRANGLER[*]}"

command -v python3 >/dev/null 2>&1 || die "python3 is needed to read the D1 id."
"${WRANGLER[@]}" whoami >/dev/null 2>&1 || die "Not logged in. Run: npx wrangler login"

# --- 1. D1 database ---------------------------------------------------------
say "Ensuring the D1 database '$DB_NAME' exists"
"${WRANGLER[@]}" d1 create "$DB_NAME" >/dev/null 2>&1 && echo "created." || echo "already exists — reusing."

DB_ID="$("${WRANGLER[@]}" d1 list --json 2>/dev/null \
  | python3 -c "import sys,json; print(next((d.get('uuid','') for d in json.load(sys.stdin) if d.get('name')=='$DB_NAME'), ''))")"
[ -n "$DB_ID" ] || die "Could not determine the database id from 'wrangler d1 list'."
echo "database_id = $DB_ID"

# --- 2. Wire the id into wrangler.toml --------------------------------------
say "Writing the database id into wrangler.toml"
if grep -q "REPLACE_WITH_D1_DATABASE_ID" wrangler.toml; then
  perl -pi -e "s/REPLACE_WITH_D1_DATABASE_ID/$DB_ID/" wrangler.toml
  echo "patched."
elif grep -q "database_id = \"$DB_ID\"" wrangler.toml; then
  echo "already set."
else
  perl -pi -e "s/database_id = \"[^\"]*\"/database_id = \"$DB_ID\"/" wrangler.toml
  echo "updated to the current id."
fi

# --- 3. Apply the schema (idempotent) ---------------------------------------
say "Applying the D1 schema (migrations/0001_init.sql)"
"${WRANGLER[@]}" d1 execute "$DB_NAME" --remote --yes --file=./migrations/0001_init.sql
echo "Tables now present:"
"${WRANGLER[@]}" d1 execute "$DB_NAME" --remote --yes --command \
  "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name" || true

# --- 4. Build the static site -----------------------------------------------
say "Building the static export (next build -> out/)"
if command -v pnpm >/dev/null 2>&1; then
  NEXT_TELEMETRY_DISABLED=1 pnpm exec next build
else
  NEXT_TELEMETRY_DISABLED=1 npx --yes next build
fi
{ [ -f out/e/_.html ] || [ -f out/e/_/index.html ]; } || die "Export shape unexpected: out/e placeholder missing."
[ -f out/_redirects ] || die "out/_redirects missing (public/_redirects should be copied)."
echo "Export OK (placeholder shells + _redirects present)."

# --- 5. Deploy --------------------------------------------------------------
say "Deploying to Cloudflare Pages ($PROJECT)"
"${WRANGLER[@]}" pages deploy out --project-name "$PROJECT"

# --- 6. Device-token secrets (you paste them; nothing else sees them) -------
say "Setting the two device-token secrets"
cat <<EOF
Use the SAME two values the Vercel deployment used, so the (currently offline) door still
matches. wrangler will prompt you to paste each one; it is read directly by wrangler.
Press Enter to set RELAY_DEVICE_TOKEN, then RELAY_VISITOR_DEVICE_TOKEN. (Ctrl-C to skip and
set them later.)
EOF
read -r _
"${WRANGLER[@]}" pages secret put RELAY_DEVICE_TOKEN --project-name "$PROJECT" || echo "(skipped — set it later)"
"${WRANGLER[@]}" pages secret put RELAY_VISITOR_DEVICE_TOKEN --project-name "$PROJECT" || echo "(skipped — set it later)"

# --- 7. What's left for you (2 clicks + the door env) -----------------------
say "Almost done. Two manual steps remain:"
cat <<EOF

  A. Bind the custom domain (Cloudflare dashboard):
       Pages -> $PROJECT -> Custom domains -> Set up a custom domain -> $DOMAIN
     (Cloudflare adds the DNS record; the apex/www personal site is untouched.)

  B. When the Pi + NUC are back online, point the door at the new relay (no code change):
       door-visiond:  VISIOND_RELAY_BASE_URL=https://$DOMAIN   (+ VISIOND_RELAY_PUBLIC_URL if set)
       door-api:      DOOR_API_VISITOR_RELAY_BASE_URL=https://$DOMAIN  (+ ..._PUBLIC_URL if set)
     then restart both services.

Verify now:
   curl -s https://$DOMAIN/api/health
   open  "https://$DOMAIN/e/inv_test#s=x&k=y"   # the enroll shell should render + block

Full checklist + rollback: docs/runbooks/relay-cloudflare-deploy.md
EOF
say "Deploy script finished."
