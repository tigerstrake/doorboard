# Token Rotation

**Status:** Verified
**Walkthrough Date:** 2026-07-08 (Simulated/mock mode verified; hardware-specific steps marked and deferred)

## The Guarantee This Runbook Protects

Per the trust model ([ARCHITECTURE.md](../../ARCHITECTURE.md) §2), the hallway-facing door Pi is considered a medium-to-low trust device because it is physically stealable. To prevent a stolen Pi from accessing sensitive control plane APIs or the NAS backup share, the Pi is issued limited-scope service tokens. If the Pi is stolen or compromised, these tokens can be instantly revoked and rotated on the NUC.

---

## Symptoms

- **Physical Theft:** The locked door enclosure is broken open and the Pi 5 or its USB SSD is missing.
- **Credential Leak:** Admin or ingest tokens are accidentally committed to public repositories, or logs show unauthorized access attempts.

---

## Step-by-Step Rotation Procedure

### Step 1: List Active Tokens on the NUC
Before revoking, identify the active service tokens assigned to the compromised door Pi:
1. Log in to the NUC control plane.
2. Run the admin CLI utility to list all registered service tokens:
   ```bash
   uv run python -m control_plane_api.cli list-tokens
   ```
3. Locate the `token_id` and labels for the tokens scoped to the stolen Pi (e.g., `ingest` or `config` scopes).

### Step 2: Revoke the Compromised Tokens
1. Revoke the config/ingest tokens immediately:
   ```bash
   uv run python -m control_plane_api.cli revoke-token <stolen-token-id>
   ```
2. Repeat for all tokens that were stored on the compromised Pi.
3. *Verification:* Verify they are removed from the database:
   ```bash
   uv run python -m control_plane_api.cli list-tokens
   ```
   Any incoming connection using the old tokens will now immediately receive a `401 Unauthorized` or `403 Forbidden` response and be blocked from writing events or reading configuration details.

### Step 3: Rotate MQTT and NAS Passwords
If the MQTT username/password and NAS limited credentials were saved in the Pi's `.env`:
1. Change the MQTT passwords in the NUC's `.env` file (`MQTT_PI_PASSWORD`).
2. Restart the NUC MQTT broker to load new credentials:
   ```bash
   docker compose -f infra/compose/docker-compose.yml restart mosquitto
   ```
3. On the NAS manager interface (Hardware-specific step), change the password of the limited service account used by the Pi.

### Step 4: Issue New Tokens for the Replacement Pi
1. On the NUC, generate new tokens with specific scopes:
   ```bash
   # Issue new ingest token (for uploading events/messages)
   uv run python -m control_plane_api.cli issue-token --door-id primary --scope ingest --label "pi-primary-ingest"

   # Issue new config token (for syncing settings/presence)
   uv run python -m control_plane_api.cli issue-token --door-id primary --scope config --label "pi-primary-config"
   ```
2. Copy the generated raw tokens.
3. On the new Pi installation, write these values to `/mnt/ssd/doorboard/.env` as `SYNC_INGEST_TOKEN` and `SYNC_UPLOAD_TOKEN`.

---

## Verification

1. Verify that a client attempting to use the revoked token is rejected:
   ```bash
   curl -I -H "Authorization: Bearer <revoked-token>" http://localhost:8090/config/door/primary
   ```
   *Expected output:* `HTTP/1.1 401 Unauthorized` or `HTTP/1.1 403 Forbidden`.
2. Verify that the new replacement Pi successfully authenticates and syncs configurations using the newly issued tokens.
