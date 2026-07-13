# deploy/pi-bird — dedicated AvianVisitors node

AvianVisitors runs only on the assigned spare Raspberry Pi 4 beside the bird
microphone. It is a control-plane sensor, never a door-plane service. Do not
install it on the door Pi 5, the ADS-B Pi, the NUC, or the NAS.

The NUC-hosted `wallboard-worker` polls the bird Pi's read-only recent-species
API. A bird Pi, NUC, or network outage therefore makes only the bird tile stale;
it cannot delay `button -> ESP32 feedback -> local UI`.

## Supported host and hardware

- Assigned host: spare Raspberry Pi 4 (2 GB or more recommended).
- OS: Raspberry Pi OS Lite 64-bit Bookworm (Debian 12) or newer. The guarded
  installer also permits x86_64 Debian 12+ for hardware-free validation, but
  the production assignment remains the Pi 4.
- Audio: a USB window/boundary microphone or a weather-protected outdoor mic
  through a USB audio adapter. Confirm it appears under `arecord -l` before
  installing.
- Storage: at least a 32 GB microSD card. This is isolated bird-detection
  storage, not door recordings and not an active NAS tier.
- Access: a non-root login with passwordless `sudo`, Git, and outbound internet
  access during the initial install.

Use a stable hostname or DHCP reservation such as `birdnet.local`. Keep the
microphone sheltered from rain, wind, and condensation.

## Security and privacy boundary

The pinned upstream LAN install serves HTTP without API authentication by
default and includes administrative pages on the same host. On the router or
host firewall, allow TCP 80 to the bird Pi only from the NUC (and a designated
admin workstation when maintenance requires it); allow SSH only from the
management network. Deny other VLANs, never port-forward it, and do not enable
AvianVisitors' Cloudflare forwarding, MQTT bridge, Home Assistant forwarder,
Gemini generation, or other cloud paths.

Basic Auth is recommended as defense in depth. Generate a hash on the bird Pi:

```bash
caddy hash-password
```

Then add this inside the existing site block in `/etc/caddy/Caddyfile`, replacing
the username and hash, and validate before reloading:

```caddyfile
basic_auth /avian/api/* {
    doorboard REPLACE_WITH_CADDY_HASH
}
```

```bash
sudo caddy fmt --overwrite /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
```

Set the same username/password only in the NUC's uncommitted `.env` as
`AVIAN_VISITORS_BASIC_USER` and `AVIAN_VISITORS_BASIC_PASSWORD`. Upstream's
Caddy generator overwrites its configuration during an upstream reinstall or
upgrade, so reapply and validate this matcher after either operation.

As post-install hardening, the Doorboard installer removes upstream's
unrestricted Caddy `NOPASSWD: ALL` sudo rule and keeps the narrower
AvianVisitors service allowlist. It also disables the browser terminal, live
audio stream, and Icecast services; none is required for the read-only summary
API. The verifier fails if those services or the broad sudo rule return.

AvianVisitors/BirdNET-Pi is licensed CC-BY-NC-SA-4.0 and is for
non-commercial use only. Confirm the upstream license is compatible with the
deployment before installation.

## Fresh installation

The Doorboard installer is fresh-install-only and pins upstream commit
`1b33a3cbc4f3b1fe0f9987e2a381ef970283931f`. It does not use upstream's
`curl | bash` bootstrap, does not track branch HEAD, and does not reboot.

Copy this directory or the repository to the bird Pi, then run as the normal
bird-node user with explicit coordinates:

```bash
cd /path/to/doorboard
export DOORBOARD_BIRD_NODE=1
export AVIAN_VISITORS_LATITUDE='REPLACE_WITH_LATITUDE'
export AVIAN_VISITORS_LONGITUDE='REPLACE_WITH_LONGITUDE'
deploy/pi-bird/install-avian-visitors.sh
```

The marker is intentional protection against running the installer on the door
Pi. The installer rejects root, password-prompting sudo, 32-bit/unsupported
systems, missing coordinates, and any existing BirdNET-Pi install. It fetches
the exact SHA into `~/BirdNET-Pi`, verifies it, invokes the checked-out
`scripts/install_birdnet.sh` directly, and verifies the SHA again.

Review the successful output, then perform the one operator-controlled reboot:

```bash
sudo reboot
```

After reconnecting, verify the microphone, services, pin, retention cron, and
recent API JSON:

```bash
cd /path/to/doorboard
export DOORBOARD_BIRD_NODE=1
deploy/pi-bird/verify-avian-visitors.sh
```

If Basic Auth protects the API, export `AVIAN_VISITORS_BASIC_USER` and
`AVIAN_VISITORS_BASIC_PASSWORD` for the verification command. Override
`AVIAN_VISITORS_URL` only when the node cannot resolve `birdnet.local`.

## Retention controls

The installer writes these upstream-supported values to
`/etc/birdnet/birdnet.conf`:

| Setting | Value | Effect |
|---|---:|---|
| `CONFIDENCE` | `0.70` | Discard low-confidence detections before storage |
| `PRIVACY_THRESHOLD` | `1` | Suppress chunks whose model results indicate human audio |
| `FULL_DISK` | `purge` | Purge old local data instead of stopping detection |
| `PURGE_THRESHOLD` | `80` | Start disk-pressure cleanup at 80% used |
| `MAX_FILES_SPECIES` | `25` | Keep at most 25 older clips per species; upstream separately preserves the newest seven days |
| `AUTOMATIC_UPDATE` | `0` | Never move the reviewed Git pin automatically |

The upstream `disk_check.sh` and `disk_species_clean.sh` cron jobs remain
enabled. The weekly `update_birdnet.sh -a` job is removed so the checkout
cannot silently advance. Audio and detection clips stay local to the bird Pi;
Doorboard does not implement audio processing, upload them to the NUC/NAS, or
place them on any door-plane storage. Disk-pressure and per-species cleanup are
both required because the newest-seven-day exception can exceed the per-species
count during a busy week.

## Upgrade and rollback

Do not run upstream `update_birdnet.sh` and do not switch the installed clone to
`avian-visitors` branch HEAD. An upgrade starts with a reviewed upstream commit
and a Doorboard PR updating both scripts, tests, and this documented pin.

For a production upgrade:

1. Back up local BirdNET data to removable/admin storage with
   `~/BirdNET-Pi/scripts/backup_data.sh -a backup -f /safe/path/birdnet.tar`.
2. Keep the current card untouched for rollback and stage a fresh 64-bit OS on
   a new card (or a spare Pi 4).
3. Run the reviewed Doorboard fresh installer, reboot once, and run verification.
4. Restore only after the clean pinned node passes; run verification again and
   reapply the Basic Auth matcher.
5. Move the stable hostname/IP to the new node. The NUC worker will recover on
   its next interval; until then the existing tile remains stale.

Rollback means restoring the old card/node and stable hostname, or rebuilding
at the previous reviewed pin and restoring the backup. Never merge or deploy an
unreviewed pin change.

## Legacy BirdNET-Go provider

`wallboard-worker` still supports `BIRD_PROVIDER=birdnet_go` for an existing
BirdNET-Go node. This deployment directory standardizes new bird Pi installs on
AvianVisitors; it does not migrate or delete a legacy node automatically.
