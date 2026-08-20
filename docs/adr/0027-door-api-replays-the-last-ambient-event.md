# ADR-0027: door-api replays the last ambient event to a reconnecting kiosk

**Status:** Accepted · **Date:** 2026-08-17

Extends the `/ws` transport (ARCHITECTURE.md §7) and `door_api.mqtt_bridge`, the NUC
ambient/presence bridge. No contract change; no new event type. Does not touch the door
interaction path.

## Context

The owner asked why the dining recommendation never appears on the wallboard. Tracing it end
to end, the transport was healthy — the bridge showed 192 messages received, 192 broadcast, 0
errors, and a 150 s listen on `/ws` carried live aircraft — and the recommender itself worked,
returning a real pick when run by hand. Nothing was broken. Three correct designs simply
compose into a channel that cannot work:

1. `WALLBOARD_FOOD_INTERVAL_S` is **86400** — the dining job publishes once a day. The
   satellite job is 3600.
2. The control plane publishes to MQTT as `client.publish(topic, payload)`. Paho defaults to
   `retain=False`, so the broker keeps no last value per topic.
3. The wallboard sets `foodRecommendation` only from a live `ambient.food_recommendation`
   arriving on `/ws`. Nothing fetches current ambient state at load, and door-api's snapshot
   is session-only.

So the wallboard could only ever show a dining recommendation if it happened to be connected
at the exact instant of that once-a-day publish. Every reload — kiosk restart, door-ui
restart, a refresh — discarded it, with no way to recover for up to 24 hours. The event was
produced, published, bridged and delivered perfectly into a browser that had already gone.

This is the same shape as the `media.storage_status` gap: an event that exists everywhere
except the surface meant to show it. A once-a-day channel is not a rare edge case for this
failure — it is the case where it is *guaranteed*.

## Decision

**door-api remembers the most recent `ambient.*` event per type and replays them to each new
`/ws` client**, after the session snapshot, in the ordinary `{"type": "delta", "event": {…}}`
envelope.

The envelope choice is the whole point: a reconnecting wallboard sees exactly what a
wallboard listening all along would have seen, so the UI needs **no new code path** — the
existing `client.subscribe("ambient.food_recommendation", …)` fires as usual. An `ambient`
block bolted onto the snapshot would have meant a second way to receive the same information,
and two paths that can disagree.

**Replay comes after the snapshot, and is dropped first under pressure.** The client queue is
bounded; if it fills, the session snapshot is what the door needs to render at all and the
ambient nice-to-have is what gets truncated. A failing replay source is caught and logged —
connecting must never depend on it.

**`ambient.*` only, deliberately not `status.*`.** `status.*` carries presence. Presence is
personal, it has its own staleness contract on the control plane
(`CONTROL_PLANE_PRESENCE_STALE_AFTER_S`), and caching it would change a live-only signal into
a remembered one: a client connecting later would be told where someone was several minutes
ago, which it would otherwise never learn. Aircraft overhead, tonight's satellite pass and
what is for lunch carry no such weight. This keeps ARCHITECTURE.md §9 intact — the cache holds
no personal data, and adds no persistence: it is in-memory and dies with the process.

**Entries expire at `DOOR_API_AMBIENT_CACHE_MAX_AGE_S`, default 26 h.** Just over the daily
producer interval, so a normal cadence always survives a reload, but a dead producer stops
being quoted. A stale ambient reading presented as current is worse than an empty tile: after
a multi-day outage the dining channel must come back blank rather than confidently serving
last Tuesday's lunch.

**The remembered type count is bounded (16).** The type arrives off an MQTT topic, so a
renamed or misbehaving producer must not be able to grow this without limit.

## Alternatives considered

**Publish ambient topics with `retain=True`.** A one-line change at the source, and every
MQTT consumer would benefit. Rejected as the primary fix because it does not solve the
reported problem: retention gets the last value to *door-api* after a door-api restart, but a
browser reload leaves door-api still holding nothing to give it. It remains a reasonable
future addition at the broker level; it is not a substitute for this.

**Have the wallboard fetch ambient state over HTTP at load.** A new endpoint, a new fetch, and
a second representation of events that already have one — with the load-time path and the
live path free to drift. The replay reuses the transport that already works.

## Consequences

- door-api now holds a little state it did not produce. That is the cost of being the only
  component both the producer chain and the kiosks can see.
- Whether the dining channel appears at all still depends on `FEATURE_FOOD` being enabled for
  the NUC's wallboard-worker, which defaults to false and could not be verified from here
  (`deploy/nuc/` is documentation only, and the host is not reachable by key). This ADR fixes
  the display side; it does not turn the producer on.
- Every low-frequency ambient channel benefits, not just food — the satellite pass at 3600 s
  had the same hole.
- A wallboard reconnecting mid-outage may briefly show an ambient reading up to 26 h old.
  Panels already render their own recency (the satellite dome computes pass phase from
  `rise_at`/`set_at`; the food payload carries `date`), so this is visible rather than silent.
