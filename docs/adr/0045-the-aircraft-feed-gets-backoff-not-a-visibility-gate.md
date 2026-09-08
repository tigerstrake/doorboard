# ADR-0045: the aircraft feed gets backoff, not a visibility gate

**Status:** Accepted · **Date:** 2026-09-09

## Context

ADR-0038 gated the relay poll on real work and closed by naming the next candidate:

> **The largest remaining ungated external poller is the aircraft feed** at 30s — roughly
> 86,000/month to adsb.fi, running whether or not anyone is looking at the wallboard.

`docs/audit-findings-todo.md` turned that into an instruction — "Gate on wallboard visibility" —
and paired it with a second, separate defect: no backoff on a sustained outage.

The two halves turned out to be nothing alike. The backoff was a real bug with a contained fix.
The visibility gate is not buildable as written, and the obvious implementations of it do not
work. This ADR records why, so the item stops being re-picked up as though it were merely
undone.

## What exists today

There is **no signal, anywhere in the system, that reports whether a wallboard is being
looked at.** Establishing that took reading the whole path:

- The wallboard is the same React app as the doorpad, on route `/wallboard`, and it opens the
  **same** `/ws` subscription with the **same** filters. The route does not change what it asks
  for.
- door-api tracks connected clients (`broadcast.py` `_clients`, `client_count`), but the set is
  anonymous: doorpad, wallboard, admin tabs, and door-voice — a permanent `/ws` client — are
  indistinguishable. Per-client filters live in a local closure in the handler and are never
  stored. `client_count` is referenced only from tests; it is on neither `/health` nor
  `/metrics`.
- The wallboard never reports anything outbound. No `visibilitychange`, no heartbeat, no
  `sendBeacon`. `ConnectionLiveness` is the client judging the *server's* silence, and is never
  sent back.
- Wallboard focus is one-way, doorpad → wallboard: `POST /wallboard/focus` builds an ephemeral
  message, broadcasts it, and stores nothing. Nobody knows which channel is on screen.
- control-plane-api has no subscriber registry at all. Fan-out is fire-and-forget MQTT, and
  Mosquitto never reports subscriber counts back to a publisher. The `/ingest` response carries
  per-event stored/duplicate results and nothing about demand.

## Decision

**Implement the backoff. Decline the visibility gate.**

The backoff shipped as part of the wallboard-worker scheduler change: a job that raises has its
next run doubled up to a 30-minute ceiling, resetting on the first success. That is the half
that was actually broken — a dead feed was being retried at full rate indefinitely, which is
worst for the provider precisely when the provider is already struggling.

The gate is declined, for three independent reasons, any one of which is sufficient.

### The available signal would not save a single call

The kiosk is a permanently-running Chromium. Its socket stays open with the monitor off, and
door-voice holds a second `/ws` connection for the life of the process. `client_count > 0` is
therefore true essentially always. Gating on "is anyone connected" would skip approximately zero
polls while adding a cross-service dependency to the ambient path. It would look like a fix and
measure as nothing.

### Per-tile gating contradicts how the wallboard renders

In ambient mode the wallboard draws every tile in a grid simultaneously. "The flights tile is on
screen" is true whenever the wallboard is on. Channel-aware gating only becomes meaningful during
a two-minute focus, so it would have to be paired with converting the ambient grid into a
rotation — a redesign of the wallboard to save calls on a feed that is not metered.

### The fact is on the wrong machine

The demand fact would live in door-api's memory on the Pi. The poller is wallboard-worker on the
NUC. Traffic is configured Pi → NUC; there is no NUC → Pi route, and wallboard-worker holds a
`control_plane_url` and nothing else. Every way across costs something real: routing it through
door-sync writes a "someone is watching" row into the NUC `events` table, which is already
flagged as unbounded; a new door-api endpoint reverses the established direction and adds a
poller to remove a poller; giving door-api an MQTT publisher breaks `mqtt_bridge`'s deliberate
subscribe-only design.

That is a large amount of new architecture to buy a reduction in calls to an **unmetered** feed
that is **within its stated limits**, on a system whose defining constraint is that nothing may
be added to the door's critical path. ADR-0038 was worth it because the relay poll was burning a
metered free tier toward a deployment pause, and because the demand fact already existed
in-process, on the same machine, as durable state. None of those three properties hold here.
Copying the shape of that fix without its justification would be cargo cult.

### What stays as the lever

`WALLBOARD_AIRCRAFT_INTERVAL_S` is unchanged at 30s. Doubling it halves the monthly volume and
is a one-line env change with no architectural cost; an ambient glanceable tile does not need
sub-minute freshness. That is left as the owner's call rather than taken silently, because it
changes how a tile they use behaves, and the poll budget is not currently in breach.

## Consequences

- The aircraft feed keeps its `demand_gated=False` entry in `tests/test_external_poll_budget.py`,
  now pointing at this ADR so the entry reads as a decision rather than an omission. It stays
  inside `MONTHLY_BUDGET_WARN` at ~86,000/month; if that ceiling is ever lowered, the interval is
  the lever, not a gate.
- A sustained adsb.fi outage now decays to one attempt per 30 minutes instead of 2,880 a day.
- **What would reopen this:** the wallboard growing a genuine visibility signal for its own
  reasons — screen blanking, a power-saving requirement, or per-tile rotation replacing the
  ambient grid. If any of those lands, the gate becomes cheap and should be revisited. It should
  not be built *in order* to gate the feed.
- ADR-0038's closing line is superseded on this one point: the aircraft feed is not the next
  candidate for on-demand gating, because it has no demand to detect. The budget table remains
  the right mechanism — it caught this, made the cost visible, and forced the question.
