# door-voice

Says "Hi &lt;name&gt;" when the door recognises someone who has opted in.

See [ADR-0034](../../docs/adr/0034-saying-a-name-out-loud-is-a-louder-disclosure.md) for why
this is a separate service, off by default, and opt-in per person.

## Why it isn't part of door-api

door-api owns `button → ESP32 feedback → local UI`, and CLAUDE.md §1 says nothing new may sit on
that path. Speech spawns subprocesses and can block on a missing or busy audio device, so it
lives out here: if `door-voice` wedges or dies, the bell still rings and the doorpad still
responds. It only ever *reads* door-api's `/ws` broadcast, holds no credentials, writes no
files, and makes no call off the Pi.

Reusing the ESP32's speaker was rejected for the same reason — it would put synthesis behind the
microcontroller that owns instant feedback.

## Hardware

The Pi has **only HDMI audio**, and the 7" touchscreen has no speakers:

```
$ aplay -l
card 0: vc4hdmi0 ...
card 1: vc4hdmi1 ...
$ lsusb | grep -i audio   # nothing
```

So a USB audio dongle plus a powered speaker is required. Until one is present the service runs
and logs `no_tts_backend_installed_service_will_stay_silent`, which is also its default state.

## Bring-up

1. Plug in the dongle, then find it:
   ```
   aplay -l                       # note the card number, e.g. card 2
   speaker-test -D plughw:2,0 -c1 -t sine   # confirm you hear something
   ```
   Put `plughw:2,0` in `VOICE_ALSA_DEVICE`.

2. Install a synthesiser. `espeak-ng` is tiny and works with no model:
   ```
   sudo apt-get install -y espeak-ng
   ```
   For something less robotic, install [piper](https://github.com/rhasspy/piper), download a
   voice `.onnx`, and set `VOICE_PIPER_VOICE` to its path. piper is only used when that path is
   set — otherwise the service falls back to espeak-ng rather than failing every call.

3. Find the person_id to opt in. It is the same opaque id the door already uses; it is *not* the
   display name.

4. Set in `/etc/doorboard/doorboard.env` (or `~/doorboard/.env` on the home layout):
   ```
   FEATURE_VOICE_GREETING=true
   VOICE_GREETING_ALLOW=prs_xxxxxx
   VOICE_ALSA_DEVICE=plughw:2,0
   ```

5. **Update the About screen first.** It currently tells visitors that being enrolled "changes a
   greeting and a colour". That is true of a screen and not of a speaker, and ADR-0034 treats
   fixing it as a precondition, not a follow-up.

6. `sudo systemctl restart door-voice` and check `journalctl -u door-voice -f`.

## Why it stayed quiet

`greeting_suppressed` logs a reason at debug level, because `not_opted_in` is the steady state
for most people and would otherwise fill the journal:

| reason | meaning |
| --- | --- |
| `feature_disabled` | `FEATURE_VOICE_GREETING` is false |
| `not_opted_in` | recognised, but not in `VOICE_GREETING_ALLOW` |
| `quiet_hours` | inside `VOICE_GREETING_QUIET_HOURS` |
| `cooldown` | greeted this person within `VOICE_GREETING_COOLDOWN_S` |
| `no_person_id` | an unrecognised visitor — no name, and no consent |
| `no_display_name` | recognised but carrying no usable name |

Run with `--log-level DEBUG` to see them. The checks run in that order deliberately: reporting
`quiet_hours` for someone who never opted in would read like "it would have said your name at
9am".
