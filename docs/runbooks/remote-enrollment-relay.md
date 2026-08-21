# Runbook: remote enrollment relay

Operating the phone-enrollment path (ADR-0016). Applies to `apps/public-relay` on Vercel and the relay client inside door-visiond.

**First thing to know when something is wrong:** none of this can affect the door. The button, the ESP32, the local UI, recording, and at-door enrollment all keep working with the relay dead, unreachable, or deleted. If someone reports the door misbehaving, the relay is not your suspect.

## Normal operation

1. The invite is minted one of two ways: the visitor taps **Enroll my face** on the DoorPad
   (self-service, ADR-0019, capped at `VISIOND_SELF_ENROLL_PER_HOUR`), or the owner runs
   `scripts/enrollment-qr "Name"` which mints via the admin API and prints the QR.
2. door-visiond mints a single-use invite, registers `sha256(secret)` with the relay, and returns a URL whose fragment carries the door key fingerprint.
3. The enrollee scans it, reads consent, takes photos, and their phone seals everything to the door's public key.
4. The relay holds the ciphertext (≤15 min). The Pi collects it on its next poll (default 5 s), decrypts, embeds, wipes, and acks.
5. The invite flips to `consumed`. The person is recognised at the door within a frame or two.

Expected end to end: a few seconds from *Encrypt and send* to *You're enrolled*.

## Health checks

```bash
# Relay: is it up, configured, and has the door checked in?
curl -s https://<deployment>/api/health

# Door: does the Pi think the relay is reachable?
curl -s -H "Authorization: Bearer $DOOR_VISIOND_ADMIN_TOKEN" \
  http://<door>:8081/relay-status
```

`GET /health` on door-visiond also carries `relay_status` and `relay_configured`. Prometheus counters: `door_visiond_relay_polls_ok`, `_polls_failed`, `_bundles_enrolled`, `_bundles_rejected`, `_consecutive_failures`.

## Symptoms

### The QR page says "the door device has not checked in"

The relay has no door key, so nothing can be sealed. The Pi publishes it on startup and every ~5 min.

1. Is the Pi's poller running? `GET /relay-status` → `{"configured": false}` means `VISIOND_RELAY_BASE_URL` or `VISIOND_RELAY_DEVICE_TOKEN` is unset. Set both, restart door-visiond.
2. `"status": "degraded"` with `last_error` set means the Pi cannot reach the relay. Check the Pi's egress and DNS, then `curl -sS https://<deployment>/api/health` **from the Pi**.
3. Token mismatch shows up as repeated failures with the relay logging 401s. Compare `RELAY_DEVICE_TOKEN` (Vercel) against `VISIOND_RELAY_DEVICE_TOKEN` (Pi).
4. `enrollment_locked` in `/health` means the encrypted volume never unlocked — see [enrollment-encrypted-volume.md](enrollment-encrypted-volume.md). The sealing key lives on that volume, so remote enrollment stays unavailable until it is unlocked. This is by design.

### The phone says "the door's key does not match the code you scanned"

The key served by the relay is not the one the QR was generated for. **Nothing was uploaded** — the client refuses to seal.

Benign cause, and by far the most likely: the door key was rotated (or the enrollment volume was rebuilt) after that QR was generated. Mint a fresh invite.

If a fresh invite from the current door also mismatches, stop and treat it as a possible relay compromise: the deployment may be serving a substituted key to make itself able to decrypt. Do not work around it. Take the deployment down, use the at-door flow, and compare the deployed source against git.

### The phone sits on "Waiting for the door", then times out

The Pi never collected. The ciphertext expires on its own within 15 minutes; nothing leaks.

- Privacy mode on → collection is suspended deliberately. `/health` shows `privacy_enabled: true`. Turn it off and re-enrol.
- Relay degraded → see above.
- Pi offline → bring it back; the enrollee needs a new invite if the bundle expired.

### "Not quite" with a reason

| Reason shown | Meaning | Fix |
|---|---|---|
| `quality_too_low` | No frame met the enrollment quality threshold | Re-enrol in brighter, even light; no hat/sunglasses |
| `invite_already_consumed` | Invite already used | Mint a fresh invite |
| `invite_expired` | Expired before collection | Mint a fresh invite; consider raising `VISIOND_RELAY_INVITE_TTL_S` |
| `invite_secret_mismatch` / `unknown_invite` | The Pi does not recognise this invite | Mint a fresh invite. Repeated occurrences with valid QRs warrant a relay-integrity look |
| `stale_consent` | Consent text changed mid-enrollment | Reload the page and start again |
| `privacy_mode` | Recognition off at the door | Turn privacy mode off |
| `enrollment_storage_locked` | Encrypted volume locked | Unlock it, then re-enrol |
| `display_name_taken` | Someone enrolled already answers to that name (ADR-0019 §2) | Re-enrol with a different name; check `GET /people` for who holds it |

## Rotating the door sealing key

Do this if you suspect the relay was compromised, or on a schedule.

```bash
curl -sS -X POST -H "Authorization: Bearer $DOOR_VISIOND_ADMIN_TOKEN" \
  http://<door>:8081/relay-key/rotate
```

- A new `door_key_id` and fingerprint take effect immediately and are republished.
- **Every outstanding QR stops verifying** — its fragment pins the old fingerprint. Mint fresh invites.
- The retired private key is kept so bundles already in the relay still open, then deleted once past `VISIOND_RELAY_RETIRED_KEY_TTL_S` (default 1 h, minimum 15 min — it must exceed the relay's 15-minute ciphertext TTL).

Rotating does **not** affect enrolled people: their embeddings are unrelated to this key.

## Turning remote enrollment off

Unset `VISIOND_RELAY_BASE_URL` on the Pi and restart door-visiond. The poller never starts, no key is published, and outstanding invites become uncollectable (they expire). At-door enrollment is unaffected.

To also take the public site down: `vercel remove <project>` or disable the deployment. Nothing on the Pi depends on it.

## Deleting someone who enrolled by phone

Identical to any other enrollment — the path they arrived by is not recorded as anything special. Use the admin panel's **Unenroll**, which runs the ADR-0009 §3 deletion: purge, tombstone, WAL truncate, cache flush, ESP32 clear, archive purge. The relay holds nothing about them to delete.

## What the relay knows, for when someone asks

Ciphertext (≤15 min), invite ids with secret *hashes*, bundle status with a machine-readable reason (≤1 h), and timing. No face, no name, no embedding, no `person_id`. It holds no key that could open a bundle. See ADR-0016 §1 and §9.
