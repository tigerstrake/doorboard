/**
 * A stand-in for the browser WebSocket, for tests that drive the kiosk over the real
 * `@doorboard/event-client` path: `vi.stubGlobal("WebSocket", FakeWebSocket)` before
 * mounting, then `open()` and `emit()` server frames on the captured instance. A frame
 * pushed here goes through the same parsing and dispatch a live door-api frame would —
 * unlike posting on the BroadcastChannel, which bypasses snapshot handling entirely.
 */
export class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  send() {}

  close() {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  emit(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

/** A `session.state_changed` delta as door-api broadcasts it on `/ws`. */
export function sessionDelta(
  fromState: string,
  toState: string,
  extra: Record<string, unknown> = {}
) {
  return {
    type: "delta",
    event: {
      event_id: "018f6b2e-7c9a-7000-8000-3f9d2ba41c22",
      type: "session.state_changed",
      source: "door-api",
      occurred_at: "2026-09-25T10:00:10Z",
      monotonic_ms: 10_000,
      door_id: "primary",
      trace_id: "9d1e8a34-52a1-4b0e-9c0f-2a7d1f66b101",
      payload: {
        session_id: "ses-approach",
        from_state: fromState,
        to_state: toState,
        trigger: "e2e",
        ...extra,
      },
    },
  };
}
