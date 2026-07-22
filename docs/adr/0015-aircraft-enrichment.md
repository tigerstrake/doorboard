# ADR-0015: Enrich the ambient aircraft summary with richer per-plane detail

**Status:** Accepted · **Date:** 2026-07-22

## Context

The wallboard "overhead aircraft" feed (`ambient.aircraft_summary`, produced by
the wallboard-worker from OpenSky `states/all`) exposes only `callsign`,
`altitude_ft`, `distance_km`, and `heading` per plane. The owner wants "all the
information you can get": speed, a live map (needs lat/lon), aircraft
type/registration/operator, route, and a photo.

Two sources of richer data are available without a new paid dependency or API
key:

- The **OpenSky state vector already fetched** carries more than we surface —
  `icao24`, latitude/longitude, velocity, vertical rate, on-ground, and
  origin country are all in the same response.
- Two free public services can be queried by hex/callsign:
  **adsbdb** (`api.adsbdb.com`) for registration / type / manufacturer /
  operator and route origin/destination, and **planespotters**
  (`api.planespotters.net`) for a thumbnail photo + photographer attribution.

`ARCHITECTURE.md` §7 and ADR-0003 require every inter-service message to flow
through `packages/contracts`, and `docs/protocols/events.md` states that schema
changes require an ADR. This is a payload/schema change, hence this record.

## Decision

- **Additive, backward-compatible contract change.** Extend
  `AmbientAircraftNearby` with optional fields derived from the state vector
  (`icao24`, `latitude`, `longitude`, `ground_speed_kmh`, `vertical_rate_fpm`,
  `on_ground`, `origin_country`) and optional externally-enriched fields
  (`registration`, `aircraft_type`, `operator`, `origin`, `destination`,
  `photo_url`, `photo_attribution`). Add an optional payload-level `observer`
  (`{latitude, longitude}`) so a UI map can centre on the configured observer.
  Every new field is optional with a `None` default: older producers, the
  offline mock, and any consumer that ignores them stay valid. `aircraft_type`
  folds manufacturer + type into one human string (e.g. "Boeing 737-900") rather
  than adding a separate `manufacturer` field.

- **Best-effort external enrichment, nearest planes only.** A new
  `integrations/aircraft/src/aircraft/enrichment.py` module enriches only the
  nearest `AIRCRAFT_ENRICHMENT_MAX` (default 6) planes (the summary is
  distance-sorted). It uses `httpx` with a short per-request timeout (~3s) and a
  global wall-clock budget so one pass stays well under the ~30s aircraft poll,
  sends a descriptive `User-Agent`, and keeps an in-memory TTL cache keyed by
  `icao24` (registration/type/photo: hours) and callsign (route: minutes) so
  repeated polls do not re-hit the upstreams. Enrichment is strictly additive:
  any HTTP error, timeout, unexpected shape, or budget overrun leaves the field
  absent and the basic OpenSky summary is still emitted.

- **Feature flag.** `AIRCRAFT_ENRICHMENT_ENABLED` (default `true`) gates all
  external calls; `AIRCRAFT_ENRICHMENT_MAX` caps the per-poll fan-out. Both are
  wired as wallboard-worker settings and passed through
  `infra/compose/docker-compose.yml`. Enrichment runs only in the wallboard
  worker (NUC), never on the door critical path.

- **Wire format.** The aircraft event is dumped with `exclude_none`, so
  unresolved optional fields are omitted rather than sent as explicit nulls.

## Consequences

- `packages/contracts` gains the new optional fields and the `AircraftObserver`
  model; regenerated schemas/TS types and the seed fixture cover them, and
  `check-generated-contracts` stays green.
- No new third-party Python dependency: enrichment uses the workspace's existing
  `httpx`. adsbdb and planespotters are free, no-key, community data sources; if
  either changes shape or goes away, enrichment silently degrades to the basic
  summary. A future move to a keyed/paid data source, or adding a distinct
  `manufacturer` field, would extend this ADR rather than overload the event.
- door-ui is out of scope here; it may consume the new optional fields (map,
  photo, route) in a follow-up without any further contract change.
