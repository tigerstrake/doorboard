# tools/seed-data

Deterministic fixtures for dev, demo and CI. Nothing here is a real person, face or
location — fixture people are fictional.

## What is here

- **`events/`** — one JSON fixture per contract event type (40 today), each a complete,
  valid `DoorboardEvent` envelope. They are *generated*, not hand-written:

  ```sh
  uv run contracts export-fixtures          # rewrites tools/seed-data/events/
  ```

  `packages/contracts/tests/test_events.py::test_fixture_files_cover_every_event_type`
  fails when the catalogue and this directory disagree, so adding an event type means
  re-running the export and committing the new file. Consumers: the contracts round-trip
  tests, the TypeScript type parity tests, and anything that needs a realistic frame of a
  given type without constructing one.

## What is not here

An earlier version of this file also promised simulator scenario YAMLs, guestbook / poll /
mood / scoreboard sample content, fake presence timelines, placeholder media with valid
checksums and mock ambient data. None of that was ever built under this directory:

- Simulator scenarios are JSON in [`apps/simulator/scenarios/`](../../apps/simulator/scenarios/),
  with golden logs beside them — see the [simulator README](../../apps/simulator/README.md).
- Placeholder media is produced at runtime by door-media's mock router (`MEDIA_MODE=mock`),
  which writes real bytes with a real SHA-256 rather than shipping files.
- Ambient tiles run on their integrations' offline mocks (wallboard-worker) or on the door-ui
  `VITE_AMBIENT_MOCK` switch; there is no checked-in ambient dataset.
- There is no seeded social content or presence timeline. The social routes start empty.

If any of those become real, they belong here; this README lists what exists so nobody
looks for a file that does not.
