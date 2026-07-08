# Token Rotation

**Walkthrough Date:** July 8, 2026 (Verified successfully)

## Symptoms

- Physical theft of the door Pi device (unauthorized access to the credentials stored on its SSD).
- Suspicious activity logs in `control-plane-api` showing requests from unexpected IP addresses using the `ingest` or `config` scopes.
- Suspected leakage of the admin token or service tokens (e.g. checked into git or shared in plaintext).

## Diagnosis

1. **Verify Token Requests**: Search the `control-plane-api` logs for access logs with token mismatches or unknown IPs:
   ```bash
   docker compose -f infra/compose/docker-compose.yml logs control-plane-api | grep -i "token_auth_failed"
   ```
2. **List Active Tokens**: Access the control-plane database to list all currently active tokens:
   ```bash
   docker compose -f infra/compose/docker-compose.yml exec -T postgres psql -U doorboard -d doorboard -c "SELECT id, scope, door_id, created_at, status FROM service_tokens WHERE status='active';"
   ```

## Step-by-Step Fix

### Step 1: Revoke the Compromised Token(s)
You can revoke a specific token by updating its status to `revoked` in the database, rendering it immediately useless.
1. Run the sql update query to revoke the Pi's token:
   ```bash
   docker compose -f infra/compose/docker-compose.yml exec -T postgres psql -U doorboard -d doorboard -c "UPDATE service_tokens SET status='revoked', revoked_at=NOW() WHERE scope='ingest' AND door_id='primary';"
   ```
2. If the admin token itself was leaked, change `CONTROL_PLANE_ADMIN_TOKEN` in the NUC's `.env` file to a newly generated secure token immediately:
   ```bash
   openssl rand -hex 32
   # Edit .env and replace CONTROL_PLANE_ADMIN_TOKEN
   ```

### Step 2: Generate a New Service Token
1. Generate a new `ingest` scope token using the control plane API:
   ```bash
   curl -X POST http://localhost:8090/admin/tokens \
     -H "Authorization: Bearer <new-admin-token>" \
     -H "Content-Type: application/json" \
     -d '{"scope": "ingest", "door_id": "primary"}'
   ```
   Save the returned `"token"` string from the JSON response.

### Step 3: Deploy the New Token to the Door Pi
1. SSH into the Pi (or connect locally if network is isolated):
   ```bash
   ssh owner@door-pi.local
   ```
2. Open the configuration file at `/etc/doorboard/tokens.env`:
   ```bash
   sudo nano /etc/doorboard/tokens.env
   ```
3. Update the token environment variable:
   ```ini
   DOORBOARD_INGEST_TOKEN="<new-ingest-token-here>"
   ```
4. Restart all sync and API services on the Pi to pick up the new configuration:
   ```bash
   sudo systemctl restart door-sync door-api
   ```

## Verification

1. Verify the Pi's `door-sync` logs show successful event ingestion requests returning `200`:
   ```bash
   sudo journalctl -u door-sync -n 20 --no-pager
   # Look for: "Ingested event successfully"
   ```
2. Attempt a mock ingest call using the old revoked token and confirm it returns `401 Unauthorized`:
   ```bash
   curl -i -X POST http://localhost:8090/ingest \
     -H "Authorization: Bearer <old-revoked-token>" \
     -H "Content-Type: application/json" \
     -d '{"batch_id": "test", "events": []}'
   # Should return HTTP/1.1 401 Unauthorized
   ```
