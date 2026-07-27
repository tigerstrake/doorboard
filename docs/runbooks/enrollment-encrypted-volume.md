# Encrypted Enrollment Volume (LUKS, key-on-NUC)

**Status:** Verified on-device 2026-07-27 (door-pi + NUC). Deployed end-to-end: NUC releases the key (401 without/with-bad token, 200 with the correct token); the Pi built the LUKS2 volume, the boot-unlock unit fetches the key and re-opens the volume from a fully-closed state (reboot-survival confirmed), and door-visiond runs `enrollment_locked: false` with `enrollment.sqlite` on the encrypted mount.

**As-deployed note (live door-pi):** the door Pi runs door-visiond as `tigerstrake` from `/home/tigerstrake/doorboard` (not the repo-canonical `doorboard`/`/opt/doorboard`), so the installed unit's `ExecStart` and `enrollment-unlock.env`'s `ENROLLMENT_OWNER` are set to match. door-visiond sources `~/doorboard/.env`, so `VISIOND_ENROLLMENT_ROOT` + `VISIOND_REQUIRE_ENCRYPTED_STORAGE` go there; the `Wants=`/`After=` on door-visiond is installed as a drop-in (`door-visiond.service.d/enrollment-unlock.conf`) rather than editing the hand-adapted unit.

## The Guarantee This Runbook Protects

Implements [ADR-0009](../adr/0009-enrollment-and-biometric-data.md) §6, Option C. The door Pi's `visiond/` enrollment data (face embeddings + display names) lives on a **LUKS-encrypted volume whose passphrase is held only on the NUC**. The Pi fetches the passphrase once at boot into RAM and unlocks the volume; it is never stored on the Pi.

Outcome for a **stolen, powered-off Pi**: the enrollment DB is *ciphertext without a key*. The only degradation is that **recognition stays disabled after a cold boot until the NUC has been reachable once** — an already-accepted failure mode, because recognition is personalization, never authorization (ADR-0005 §3). The door button, local UI, recording, and sync are unaffected.

---

## Components

| Piece | Where | Role |
|---|---|---|
| `CONTROL_PLANE_ENROLLMENT_KEY` | NUC `.env` | the LUKS passphrase (authoritative, single copy) |
| `CONTROL_PLANE_ENROLLMENT_KEY_TOKEN` | NUC `.env` | narrow bearer token gating release |
| `GET /status/keys/enrollment` | NUC control-plane :8090 | releases the passphrase to a holder of the token |
| `/etc/doorboard/enrollment-key.token` | Pi, root-only 0600 | the Pi's copy of the release token (NOT the passphrase) |
| `/etc/doorboard/enrollment-unlock.env` | Pi, root-only 0600 | URL + paths for the unlock scripts |
| `scripts/create-enrollment-volume` | Pi | one-time: build the LUKS volume |
| `scripts/doorboard-enrollment-unlock` | Pi | boot: fetch key → open → mount (fail-closed) |
| `doorboard-enrollment-unlock.service` | Pi | runs the unlock, ordered before `door-visiond` |

---

## Part A — NUC side (control-plane)

1. Generate the passphrase and the release token (do not echo them):
   ```bash
   cd ~/doorboard
   printf 'CONTROL_PLANE_ENROLLMENT_KEY=%s\n'       "$(openssl rand -hex 32)" >> .env
   printf 'CONTROL_PLANE_ENROLLMENT_KEY_TOKEN=%s\n' "$(openssl rand -hex 24)" >> .env
   ```
2. Rebuild + recreate the control-plane so it ships the key-release endpoint (#134) and reads the new env (#135):
   ```bash
   cd infra/compose
   docker compose -p doorboard-nuc --env-file ~/doorboard/.env -f docker-compose.yml build control-plane-api
   docker compose -p doorboard-nuc --env-file ~/doorboard/.env -f docker-compose.yml up -d control-plane-api
   ```
3. Verify the endpoint is live and configured (401 without a token proves it is gated, not 404/unconfigured):
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8090/status/keys/enrollment   # expect 401
   ```

## Part B — Pi side (one-time)

1. Install the release token (from the NUC's `CONTROL_PLANE_ENROLLMENT_KEY_TOKEN`) root-only:
   ```bash
   sudo install -m600 -o root -g root /dev/stdin /etc/doorboard/enrollment-key.token <<<'<paste-token>'
   ```
2. Install the unlock config and adjust paths/owner for this deployment (repo-canonical uses `doorboard:doorboard`; the live Pi runs visiond as `tigerstrake`):
   ```bash
   sudo install -m600 -o root -g root deploy/pi-door/enrollment-unlock.env.example \
        /etc/doorboard/enrollment-unlock.env
   sudo vi /etc/doorboard/enrollment-unlock.env   # set ENROLLMENT_OWNER, URL, sizes
   ```
3. Create the volume (fetches the passphrase from the NUC; never writes it to disk):
   ```bash
   sudo ENROLLMENT_UNLOCK_ENV=/etc/doorboard/enrollment-unlock.env scripts/create-enrollment-volume
   ```
4. Point visiond at the mounted enrollment root and require encryption (in `doorboard.env`):
   ```
   VISIOND_ENROLLMENT_ROOT=/mnt/vision-enrollment/doorboard
   VISIOND_REQUIRE_ENCRYPTED_STORAGE=true
   ```
5. Install + enable the boot-unlock unit **and its retry timer**, then restart
   visiond. Set `ENROLLMENT_UNLOCK_NOTIFY_UNIT=door-visiond.service` in
   `enrollment-unlock.env` so a successful retry nudges visiond to re-evaluate:
   ```bash
   sudo cp infra/systemd/doorboard-enrollment-unlock.service /etc/systemd/system/
   sudo cp infra/systemd/doorboard-enrollment-unlock.timer   /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now doorboard-enrollment-unlock.service
   sudo systemctl enable --now doorboard-enrollment-unlock.timer
   sudo systemctl restart door-visiond.service
   ```

---

## Verification

1. The mount is dm-crypt / LUKS-backed:
   ```bash
   findmnt /mnt/vision-enrollment
   lsblk -o NAME,TYPE,MOUNTPOINT | grep crypt          # expect a "crypt" row
   ```
2. visiond reports the volume unlocked (`enrollment_locked: false`):
   ```bash
   curl -s http://127.0.0.1:8081/health | python3 -m json.tool | grep enrollment_locked
   ```
3. **Fail-closed test** — with the NUC unreachable, a cold boot must leave recognition disabled but the door working:
   ```bash
   sudo systemctl stop doorboard-enrollment-unlock.service
   sudo umount /mnt/vision-enrollment; sudo cryptsetup luksClose vision-enrollment
   sudo systemctl restart door-visiond.service
   curl -s http://127.0.0.1:8081/health | grep enrollment_locked   # expect true; door/UI still up
   ```
   Then bring it back:
   ```bash
   sudo systemctl start doorboard-enrollment-unlock.service
   sudo systemctl restart door-visiond.service                     # enrollment_locked -> false
   ```

---

## Recovery / rotation

- **Rotate the passphrase** (e.g. after suspected Pi theft — see also [token-rotation.md](token-rotation.md)): the passphrase is a LUKS keyslot, so add the new one and remove the old with `cryptsetup luksAddKey` / `luksRemoveKey` on the mounted container, then update `CONTROL_PLANE_ENROLLMENT_KEY` on the NUC. Do **not** just change the NUC value — that would strand the volume.
- **Rotate the release token**: update `CONTROL_PLANE_ENROLLMENT_KEY_TOKEN` on the NUC + recreate the container, then update `/etc/doorboard/enrollment-key.token` on the Pi. No re-encryption needed.
- **NUC permanently gone**: re-enroll from scratch on a fresh volume; embeddings are intentionally unrecoverable without the key.

## Residual risk (tracked)

The Pi fetches the passphrase over **plaintext HTTP on the LAN**, so a LAN traffic sniffer could capture it in transit. This is outside Option C's threat model (stolen *powered-off* Pi = ciphertext-at-rest) and consistent with the rest of the internal control-plane, but front the control-plane with TLS (the `infra/caddy` reverse proxy) to close it. Follow-up, not a blocker.

**Cold-boot race — resolved by `doorboard-enrollment-unlock.timer`.** If the Pi finishes booting before the NUC's control-plane is serving, the boot unlock fails and the volume stays locked — door-visiond runs `enrollment_locked` (recognition disabled; door/UI/recording unaffected), as ADR-0009 accepts ("disabled until the NUC is reachable once"). The retry timer now makes this **auto-heal**: because the unlock service is a `RemainAfterExit` oneshot, the timer's `OnUnitInactiveSec` re-runs it every ~2 min *only while it is failed*, and stops once it succeeds. On the successful retry the unlock script `try-restart`s `ENROLLMENT_UNLOCK_NOTIFY_UNIT` (door-visiond), so visiond re-evaluates and recognition comes up without a manual touch or reboot.
