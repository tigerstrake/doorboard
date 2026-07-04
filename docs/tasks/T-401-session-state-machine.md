# T-401: Visitor session state machine (door-api)

**Agent:** codex · **Milestone:** M4 · **Depends on:** T-002, T-003
**Why this agent:** the state machine is the spine of the visitor experience; edge-case correctness (reloads, timeouts, races) is the whole job.

## Context

Normative states/transitions: [events.md §session](../protocols/events.md); service spec: [apps/door-api/README.md](../../apps/door-api/README.md); requirements: handoff §9. The `State` enum and transition table already exist in contracts (T-002) — implement the runtime.

## Deliverables

- Explicit state machine in door-api: transition function validating against the contracts table, illegal transitions rejected + logged, every transition emitting `session.state_changed`.
- Triggers wired: `door.button_pressed` → immediate `VISITOR_MODE` (local, no awaits on anything remote); `vision.identity_stable` → `APPROACH_DETECTED`/`IDENTITY_CACHED`; ring timeout → `UNANSWERED_TIMEOUT`; owner action or door-contact → `ANSWERED`; media events → video-message states; inactivity → auto-expiry to `IDLE`.
- Persistence: session state in SQLite (WAL, SSD path) so service restart and kiosk reload rejoin the live session; expiry timers reconstructed on restart.
- WebSocket display broadcasts: snapshot-on-connect + transition deltas per api-conventions.
- Timeouts/durations in typed config with documented defaults.
- Test suite: table-driven transition tests (every legal/illegal edge), restart-mid-session, reload-rejoin, double-press, press-during-recording, timeout races. Simulator scenarios extended to cover the full happy path and three abuse paths.

## Out of scope

DoorPad flow UI (T-402), social features (T-403), wallboard rendering (T-404), personalization content (T-303 already feeds it).

## Acceptance criteria

- Button → `VISITOR_MODE` broadcast measured < 250 ms budget path on bench (T-104).
- Kill -9 door-api mid-`RINGING` → restart resumes the session correctly; kiosk reload during `VIDEO_MESSAGE_REVIEW` rejoins at the same state.
- 100% transition-table coverage in tests; illegal-transition attempts provably side-effect-free.
