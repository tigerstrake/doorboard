# Audit findings — open TODO / fix-later backlog

Provenance: two coordinator-led review rounds (2026-08-22 and 2026-08-23), verified
against code. Items marked **FIXED** below already landed (kept for reference so they
aren't re-done). Everything else is open. This is the deferred backlog; work it down by
priority, and prefer a superseding ADR over an improvised change where noted.

**Status legend:** `[ADR]` needs a superseding/new ADR or an owner decision before code;
`[FIX]` contained, code-fixable without a decision; `[DEPLOY]` lives in the installer /
systemd / host, sensitive on live hardware; `[DOC]` documentation only; `[DELETE]` remove
dead code/config.

---

## A. Security & privacy — need an ADR or an owner decision

- `[ADR]` **Retention only deletes *synced* media.** Every deletion path in
  `apps/door-media/.../service.py` (incl. the age cap, ~`:983`) is gated on
  `sync_status == 'synced'`. On a NAS outage the SSD fills to the `min_free_bytes` floor,
  `recording_allowed` flips false, and the door **stops recording indefinitely** — while an
  unbounded, `person_id`-linked archive of everyone who came to the door sits on the
  stealable Pi (violates "no long-term archive on the Pi", §2). ADR-0007's
  "never delete unverified" is binding, so this needs a superseding ADR: either a loud
  escape valve (drop unsynced clips past a hard age/space bound) or explicit acceptance of
  the recording halt.
- `[ADR]` **Enrollment invite secret rides in the URL path** (`door-visiond .../service.py`
  builds `…/e/<invite_id>.<secret>#k=<fp>`). A compromised relay reads it from its own
  request line and can enroll an attacker's face — falsifying ADR-0016 §4/E-11
  ("a fully compromised relay cannot cause an enrollment"). Move the secret into the URL
  fragment (never sent to the server), or POST it; needs a superseding ADR because the
  README + security-checklist assert the now-false conclusion.
- `[ADR]` **`/doorpad/enroll-invite` is unauthenticated on the LAN-exposed door-api.** A
  hallway stranger can mint up to 6 live remote-enroll invites/hour. ADR-0019's "standing
  at the door is the authorization" assumed a house, not a shared dorm corridor. Decide what
  authorizes invite minting now the LAN is hostile.
- `[ADR]` **`/ws` and `/session` broadcast recognized identity unauthenticated on the LAN.**
  The snapshot bypasses the allow-list the HTTP layer applies. Partially mitigated (guestbook
  + attribution leaks closed 2026-08-23), but the socket itself needs a trust tier / auth.
  Note `social.checkin_created` still broadcasts `person_id` to `/ws` (only guestbook was
  gated). Root cause: the event bus never got the public/owner split the HTTP layer got.
- `[DEPLOY]` **The live installer serves the kiosk from a Vite dev server on `0.0.0.0`**
  (`deploy/pi-door/install-home-layout`), exposing `/admin`, `/session`, `/ws`, and (until
  the proxy was flagged) camera snapshots to the whole building. The canonical
  `door-ui.service` already serves a built bundle on `127.0.0.1`; switch to it. Also remove
  the dead `/door-media` Vite proxy (reverted once; re-apply).
- `[DEPLOY]` **Root runs user-writable scripts every 30s** (health-agent + enrollment-unlock
  units point `ExecStart` at `$repo/scripts/...` with no `User=`). Compromise any door
  service → write the script → root, which holds the LUKS unlock token. Add `User=`, or move
  the scripts out of the writable checkout.
- `[ADR]` **Two MediaMTX instances.** `install-home-layout` enables a systemd `mediamtx.service`
  pointing at the same config path door-media itself spawns (there is no `MEDIA_MODE` to
  attach to an external instance). They contend for 9997/8554/8889; the loser crash-loops with
  no metric and no health change. Decide the single owner (recommend door-media, per
  ARCHITECTURE §3) or add an "external" media mode.
- ~~`[ADR/FIX]` **The personalized-greeting light has never run on hardware.**~~ **FIXED
  2026-08-23 (ADR-0040):** door-api owns the single ESP32 UART, so door-visiond now relays
  `door.profile_update`/`clear` to door-api's new `/internal/esp32/profile` route and door-api
  puts them on the wire (throttled ~1 Hz on the relay path, immediate on a changed profile).
  This also removed the latent cross-service seq-counter hazard. See "Already FIXED".
- `[DEPLOY]` **No TLS on the door plane; admin bearer + MQTT creds cross the dorm LAN in
  cleartext.** One captured `Authorization` header yields recordings, gallery, and the
  ADR-0024 proxy into door-visiond. Mosquitto is plaintext 1883 on `0.0.0.0`.
- `[ADR]` **A stolen Pi self-unlocks if it stays in wifi range** (`doorboard-enrollment-unlock`
  curls the NUC every 120s). Sound against a thief who leaves the building; not against a
  dorm-neighbor. Consider a manual-unlock or presence-gated policy; rotate the single shared
  `CONTROL_PLANE_ENROLLMENT_KEY_TOKEN`.
- `[FIX]` **Purge chain gaps** (unenrollment doesn't fully delete):
  - `session.state_changed` rows carry `display_name` but no `person_id`, so `purge.py`
    (`WHERE person_id=?`) can never reach them in the NUC archive.
  - Poll votes have **no** deletion flow anywhere; guestbook/check-in "deletion" is
    soft-delete that retains full text; deleting an entry orphans its photo on every tier.
  - door-api has **no** `/internal/purge/{person_id}` endpoint and no `DELETE` in its social
    store, so its `person_id`-linked rows survive unenrollment.
  - door-voice's `VOICE_GREETING_ALLOW` consent list is env config, outside every deletion path.
- `[FIX]` **Privacy mode doesn't stop the camera.** It suppresses door-visiond events, but
  door-media owns the camera and never learns; a LAN client polling `/snapshot/recognition`
  keeps it running. Live privacy-mode state is also not shown on public surfaces (the new
  static camera notice is a start, not the live state).
- `[FIX]` **Redaction filter is installed only in door-visiond and never scans `record.msg`**
  (`logging_setup.py` / `redaction.py`). A `logger.info(f"vec={v}")` is unfiltered despite the
  docstring's guarantee. `matcher.py` still uses a bare `getLogger` (the `best_person_id` leak
  itself was **FIXED** 2026-08-23). door-media/door-api/door-sync/control-plane don't install
  the filter.
- `[FIX]` **Raw enrollment photos written to the SSD for no functional reason**
  (`door-visiond .../service.py`: `img_path.write_bytes(image)` then `read_bytes()` when the
  embedder already takes bytes). The `finally` wipe is `rmtree` (unlink, not overwrite).
- `[DOC]` **Stale invariant docs now false:** ARCHITECTURE §9, ADR-0017's table, and
  `docs/runbooks/security-checklist.md` say "the relay never receives an enrollee's name",
  but ADR-0018 authorized `attributed_to` (a real display name) to cross the relay. Correct
  the docs. Also re-run the never-completed stolen-Pi drill and re-date the checklist.

## B. Correctness & reliability — contained, fixable

- `[DONE 417b5ab]` **door-media 422 permanently wedges door-api's media outbox.** Strict FIFO
  (`persistence.py` `ORDER BY rowid LIMIT 1`), no attempt cap, no dead-letter, no
  skip-past-poison-head. One contract-drift 422 on a partial deploy blocks all bell-clip
  recording forwarding until the outbox fills and evicts the head. Same shape for the sync
  outbox. Fixed: `dead` column + `WHERE dead = 0` head select + `max_attempts` cap on both
  outboxes (mirrors door-sync's queue); dead count on /metrics + degraded /health. Tests
  prove the wedge is gone. Needs redeploy to the Pi.
- `[DONE 1609ef3]` **door-sync SSE consumer catches the wrong exception** (`sources.py`: `except
  (JSONDecodeError, KeyError)`, but `parse_event` raises `ValidationError`), so one
  schema-invalid media event tears down the whole clip-sync stream and mislogs it as a
  disconnect. Fixed: `_handle_frame` swallows any per-frame error; only a transport failure
  reconnects. Tests pin it with a real `parse_event` ValidationError. Needs redeploy to the Pi.
- `[DONE 1609ef3]` **LED profile catalog drift (VERIFIED drift today).** `PROFILE_CATALOG` had
  `warm_amber, violet_dusk, coral_glow, cool_white`, none of which exist in the firmware's
  `door_effect_from_name` — so 4 of 6 enrollable profiles silently fell back to `blue_wave`.
  Fixed: reconciled all four profile surfaces (door-visiond, ui-kit, phone EnrollFlow, admin
  dropdown) to the firmware's six effects; new test parses the firmware source so it can't
  drift again. Needs redeploy to the Pi + phone relay.
- `[FIX]` **`INFERRED_SOURCES` is dead code** (imported nowhere); the real `tracking_enabled`
  consent gate is three inline literals in `presence_engine.py`. A new inferred presence
  source would bypass the gate silently. Either wire `INFERRED_SOURCES` as the gate or delete
  it.
- `[FIX]` **Finalize orphans a clip on restart** (door-media `service.py`): the idempotency
  memo is process-local, the lifespan does no startup reconciliation, and the media DB is
  `synchronous=NORMAL` (no fsync). A restart/power-loss mid-finalize strands a clip that no
  tool, sweep, or reconcile can find. Add a startup sweep + `synchronous=FULL`.
- `[FIX]` **door-media clip write is non-atomic on the video-only path**, there is **zero
  fsync** in door-media, and `.concat_*.txt` / `.muxed_*.mp4` / per-recording `.m4a` temp
  files leak on SIGKILL with nothing pruning them.
- `[FIX]` **door-sync reconcile never re-runs after an SSE reconnect** (`sources.py` calls it
  once at lifespan; door-media `/events` has no replay), so finalized events emitted during a
  door-media restart are lost.
- `[FIX]` **Health/metrics gaps:** door-media exports no frame/finalize/zero-byte metric; a
  black or frozen camera is undetectable end to end. `infra/monitoring/alert.rules.yml` has no
  alert on `door_media_stream_up == 0`, `door_sync_dead_letter_total > 0`, or backup failure.
  (The stream-up/queue-age health lies themselves were **FIXED** 2026-08-23.)
- `[FIX]` **Postgres restore is not error-checked** — neither the runbook nor the integration
  test passes `psql -v ON_ERROR_STOP=1`, so a partial restore reports success. (The
  backup-side false-success was **FIXED** 2026-08-23.)
- `[FIX]` **Perf harness measures none of the latency budgets it claims.**
  `tests/performance/harness.py` `bell_to_visitor_mode` never constructs a `SessionMachine`
  (times a dict→pydantic conversion; p95 ≈ 0.01ms vs a 250ms budget), and the regression gate
  floors at `max(base,1.0)×factor` so a 400× real slowdown passes. Make the harness clock
  touch a real state machine and a disk-backed SQLite.
- `[FIX]` **No startup cross-checks for any twin registry** (LED profiles, presence sources,
  doorpad effect id, event models vs union). Drift is caught at CI-test time or not at all.
  Add import-time asserts where the pairs live.
- `[FIX]` **`segment` retention / a leaked active recording pins segment retention forever**
  (`mediamtx_router.py` clamps the cutoff to `earliest_active` with no reaper), and a
  `bell_clip` is never finalized on SESSION_END with `visitor:discard` / `timeout:*` triggers.

## C. ESP32 firmware (`firmware/esp32-door-controller`) — `main.c` is untested in CI

- `[FIX]` **`main.c` is never compiled or tested in CI** — only `door_protocol.c` /
  `door_effects.c` are. Every item below lives in the one file nothing checks. Add a host
  build/test.
- `[FIX]` **`boot_id` is a hardcoded constant**, so the `(boot_id, seq)` dedupe reset never
  fires: after a watchdog reboot the Pi keeps rejecting the ESP32's frames for ~30 minutes
  (seq must climb past the old high-water) while every frame is acked. Use a per-boot random
  id on both sides.
- `[FIX]` **49.7-day `millis()` rollover** (the ISR timestamps with a 32-bit tick counter
  while everything else uses 64-bit `esp_timer`). At the wrap the cached profile never expires
  and fallback flaps on every press. Timestamp the ISR from `esp_timer_get_time()`.
- `[FIX]` **No partial-frame timeout + `strstr`-from-buffer-start** lets a stale prefix win, so
  a `profile_clear` (incl. privacy-mode clears) can be swallowed while the Pi believes it was
  acked. Discard the partial buffer on silence/`hello`; anchor field lookup.
- `[FIX]` **Oversize-line handling truncates before the length check**, so junk can synthesize
  a valid frame and `rx_errors` stays blind. Mirror the Pi's discard-until-newline.
- `[FIX]` **`hello` is sent once at boot** (never re-sent), so a Pi that starts later never
  learns the ESP32's `boot_id`/`fw_version`. Re-send on fallback edge or inbound `hello`.
- `[FIX]` **Head-of-line TX queue drops presses silently** during a Pi outage (no `tx_dropped`
  counter). **Watchdog reconfigure is discarded** (`esp_task_wdt_init` returns
  `INVALID_STATE`; use `esp_task_wdt_reconfigure`). **Fallback is a 600ms one-shot, not a
  state.** **Knock detection is a compile-disabled stub** (`const bool threshold_crossed =
  false`) — implement it or stop listing it as a current ESP32 responsibility in ARCHITECTURE.
- `[FIX]` **FreeRTOS queue API called inside `portENTER_CRITICAL`**, and a JSON parse runs with
  interrupts disabled on the button's core (`main.c` RX path). Use a mutex, or parse outside
  the lock.
- `[FIX]` **Pi-side (`packages/esp32-link`): serial `write` busy-spins holding the send lock**
  (`asyncio.sleep(0)`), and door-visiond keeps a second `_esp32_seq` counter the transport
  doesn't know about (no bug today, same defect family as the seq=0 one that was **FIXED**).

## D. Operability / cost

- `[FIX]` **Aircraft feed polls adsb.fi every 30s (~86k/mo) with no demand gate** and no
  backoff on a sustained outage (ADR-0038 named it the next candidate). Gate on wallboard
  visibility.
- `[FIX]` **wallboard-worker scheduler fires every job immediately on each (re)start** (boot
  burst) and has **no job-level backoff** — a failing external job retries at full rate
  forever. Same shape ADR-0038 fixed for the relay.
- `[FIX]` **The NAS has no retention policy** (gallery + manifest grow unbounded), and the
  **NUC `events` table is unbounded** and holds `person_id` without the `ON DELETE CASCADE`
  protection the visit log has. Add prune jobs / decide a policy (`deploy/nas/README.md` notes
  indefinite-by-default violates the handoff).
- `[FIX]` **Wall-clock used where monotonic was required** on the Pi (whose clock starts at
  1970 each boot until NTP): the visitor-token TTL (`visitor_tokens.py`) and the persisted
  retry-backoff epochs (`door-api`/`door-visiond` outboxes) collapse across the NTP
  step-forward. Fails safe today, but fragile.
- `[FIX]` **door-voice WS reconnect has no backoff** (flat 3s) — the one outlier against an
  otherwise-disciplined reconnect pattern.

## E. Docs / hygiene

- `[DOC]` **`docs/runbooks/storage-full.md`** (marked "Verified") references columns and log
  keys that don't exist. **`events.md`** `ambient.aircraft_nearby` is still missing ~14 model
  fields (ADR-0015). Failed-admin-auth is logged nowhere.
- `[DELETE]` **Dead config:** `HOME_ASSISTANT_URL` / `HOME_ASSISTANT_TOKEN` (read by nothing),
  `DOORBOARD_ENV` (read by nothing), `RetentionConfig.min_free_bytes` (populated, never
  consumed). (`SYNC_UPLOAD_TOKEN` dead-alias was **FIXED**.)
- `[FIX]` **`_frequency_stat_is_enabled()` off by default is correct, but enabling it makes the
  public shape identical to admin** (publishes `person_id` + ranking). Keep the projections
  separate even when enabled.

---

## Pending deploy (fixed in git, not yet on the devices — packed for transport 2026-08-23)

- Everything in commits `e9bdc70` + `10ad701` + the kiosk-scrollbar fix: the FR profile-light
  relay, About tile move, live satellites, presence naming, the owner-reported live-door batch
  (camera notice off the wallboard, About off the ambient grid, satellite seam hidden, flights
  zoom + type icons, video-playback patience, presence re-publish on startup, bell-takeover
  clipping, guestbook readability, real resident names, DoorPad admin unlock ADR-0042), and
  hiding the native OS scrollbars on the kiosk surfaces. Deploy NUC-then-Pi (see the deploy
  runbook), then **re-enroll the owner's face** — the July-20 templates no longer match the
  model (live match score ~0.33 vs 0.62 threshold), which is why recognition fails.
- **Verify on-device after deploy** (couldn't be checked here — devices offline): the
  video-message backend (the "preparing playback" root cause is likely the two-MediaMTX-instance
  conflict in §A — the client is patient now, but recording finalization needs an on-device
  check), and the visual CSS fixes (bell layout, guestbook size, About readability, scrollbars).

## Already FIXED (do not redo) — 2026-08-22 / 2026-08-23

pg-backup false-success + eviction; NAS mount-marker for clips **and** gallery; honest
health for door-media (`ready`) and door-sync (oldest-pending age); doorpad effect delivery
(transport `make_message` seq + `generic_press`); matcher `best_person_id` biometric-log
leak; door-voice's 42 tests wired into CI; stale presence test; events.md re-sync; Grafana
loopback + required password; 10 config/doc mismatches (TZ→wallboard-worker,
`SYNC_INGEST_TOKEN`, aircraft-observer coords, ESP32 `.env` staleness + device, VOICE/MQTT Pi
keys); guestbook unmoderated-broadcast leak; relay attribution session-binding (can't name
the wrong person); four door-ui UX fixes (honest ring, wallboard staleness watchdog + dot,
camera/privacy notice, boot-clock guard).
