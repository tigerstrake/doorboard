# Doorboard performance harness

Two runners live here. Neither is evidence for physical latency, thermals,
cameras, the Hailo, WebRTC, or hardware acceptance; both catch order-of-magnitude
code-path regressions on a machine with no door attached.

## Latency harness (`harness.py`)

```bash
uv run python -m tests.performance.harness --report-out latency-report.txt
```

CI runs exactly that and appends the report to the job summary. The exit code is
the gate: `1` if any measured path regressed against `baseline.json` or is over
its ARCHITECTURE.md §4 budget, else `0`.

### What is measured

Both measured paths run the real service code in-process, against real on-disk
SQLite stores (WAL, `synchronous=FULL`, the Pi's settings) in a temporary
directory, 50 iterations each with a fresh session per iteration.

| Path | Budget | What the clock covers |
|---|---:|---|
| `bell_to_visitor_mode` | p95 < 250 ms | door-api's real FastAPI app under Starlette's `TestClient` (lifespan on). Starts when a decoded `door.button_pressed` frame is handed to the ESP32 event-consumer's queue — where a UART frame enters the same queue on the door — and stops when the `session.state_changed → VISITOR_MODE` delta is read from the real `/ws` endpoint. In between: contract validation in the consumer, `SessionMachine.handle_button_pressed` (two transitions, each committed to the session store), the broadcast fan-out, the WebSocket send. Each session is then ended through `POST /admin/session/end` (outside the window; the `SESSION_END → IDLE` linger is zeroed by env). |
| `bell_to_recording_event` | < 500 ms | door-media's real FastAPI app on an in-loop `httpx.ASGITransport`, so the `/internal/session_event` route, its admin-token dependency and the Pydantic body validation are on the path, over the real `MediaMTXRouter`, `RecordingDB` and `RecordingService`. Starts as the `BUTTON_PRESSED` transition door-api forwards on a press is posted and stops when the response is back — by then `media.recording_started` has been put on the broadcast queue (asserted every iteration) and the processed-event mark committed after it in the same handler. The MediaMTX control API is mocked to report the visitor stream live (the row's "stream already live" precondition); `start_recording` only opens a window over segments MediaMTX writes continuously, so nothing else is asked of it. Each window is discarded after its sample. |

Not on either path, and therefore not in the numbers: the UART, uvicorn's
WebSocket framing and the loopback HTTP hop between door-api and door-media
(`TestClient`/`ASGITransport` stand in for them), and MediaMTX itself. The
door-api figure also carries two harness thread hops the Pi does not pay, so it
is a slight over-estimate.

### What is N/A, and why

The other five §4 paths are reported as `simulator_na` **by name**, with the
reason, whatever the run did. A number for any of them could only ever read
"pass", and a budget that cannot fail is worse than one marked unmeasured. The
table is `SIMULATOR_NA` in `doorboard_observability.harness_core`:

| Path | Why not here | Where instead |
|---|---|---|
| `button_to_generic_feedback` | ESP32-local: ISR → LED/audio never crosses to the Pi | bench (`tests/hardware-in-loop/`) |
| `button_to_personalized_feedback` | ESP32-local: cached profile → effect | bench |
| `tap_to_local_response` | browser-side: pointerdown → first paint on `/doorpad` needs a browser | bench |
| `face_to_stable_identity` | needs a camera and the Hailo; frame cadence, detection and embedding are the whole budget | door-visiond's own `face_to_identity_ms_p95` on `/metrics`, on the door |
| `webrtc_glass_to_glass` | needs MediaMTX and a browser | bench |

Earlier versions timed simulator stand-ins for the first three (a queue put and
a Pydantic build, ~0.005 ms against 30–100 ms budgets). Those numbers are gone
on purpose; do not reintroduce a proxy without a path that can actually miss.

### The gate

For every measured path:

- **Budget:** `p95 > budget` fails, with or without a baseline. A baseline
  saved while a path was already over budget does not make that the accepted
  state.
- **Regression:** `p95 > max(3 × baseline p95, 0.02 × budget)` fails. The floor
  keeps scheduler jitter on a sub-millisecond baseline from counting as a
  regression while still catching the smallest change the budget cares about
  (5 ms of 250, 10 ms of 500). The old fixed 1 ms floor let a 250 ms path grow
  300× unnoticed.

### Regenerating the baseline

```bash
uv run python -m tests.performance.harness --save-baseline --runs 3
```

`--runs N` collects N times; the gate judges the last run and the saved
baseline is the per-stat median across runs. `baseline.json` is committed; the
JSON carries `budget_failures`, `regressions`, and a per-path `over_budget`
flag alongside the percentiles. Regenerate it on the machine class that gates
(CI) if a legitimate change moves a path, never to make a red run green.

### `--mode hardware`

Not implemented: it raises so CI cannot pass it by accident. The bench
procedure is `tests/hardware-in-loop/T-104-latency-harness.md`. Only a report
with `hardware_acceptance: true` — which this runner cannot produce today — may
sign off ARCHITECTURE.md §4.

### `system.latency_sample` has no producer

`doorboard_observability.metrics` still ships `record_sample`,
`latency_sample_payload` and `drain_latency_events`, and the contracts catalog
still has `system.latency_sample`. Today the only `record_sample` call in any
service is door-visiond's `face_to_stable_identity`; nothing calls
`drain_latency_events`, so no service ever emits `system.latency_sample`, and
six of the seven §4 histograms on the services' `/metrics` are permanently
empty. This harness does not change that — it measures in-process, not in
production. Wiring the emission (door-api in `handle_button_pressed`, door-media
in `start_recording`, plus one periodic drain) or removing the dead path and the
catalog entry by ADR is tracked by the coordinator.

## Simulator soak (`soak.py`)

T-702 composes the simulator into a soak regression runner: a 30-minute
workload advanced without waiting 30 real minutes, checking press/observe
counts, NAS backlog recovery, resource maxima, camera frame drops, WebRTC churn
and UI reconnects.

```bash
uv run python -m tests.performance.soak --profile full   # 30 simulated minutes
uv run python -m tests.performance.soak --profile ci     # what CI runs
```

Its latency table is built with the same `build_json`, so the five N/A paths
carry the same label there; its two bell rows are still simulator stand-ins
(`FakeEsp32Transport`, `FakeMediaRouter`), not the in-process measurement
above, and are only a sanity check on the soak's own orchestration. Artifacts
go to a per-run temporary directory.

Reports are written under `tests/performance/results/`, which is gitignored:

- `soak-verdict.json`: machine-readable pass/fail, counters, latency tables,
  resource maxima, and raw resource samples.
- `soak-report.txt`: human-readable verdict with p95 tables and compact ASCII
  graphs for queue depth and CPU.

The real bench profile should keep the same verdict schema when the hardware
adapters are available. Only a report with `hardware_acceptance: true` may be
used to sign off ARCHITECTURE.md section 4 or the T-702 hardware gate.
