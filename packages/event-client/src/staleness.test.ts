// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { DoorboardEventClient } from "./index";

// A fake WebSocket we drive by hand: the client only ever touches new WebSocket(url),
// send(), close(), the onopen/onmessage/onclose/onerror handlers, and WebSocket.OPEN.
class FakeWebSocket {
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
  sent: string[] = [];
  closed = false;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    if (this.readyState === FakeWebSocket.CLOSED) return;
    this.readyState = FakeWebSocket.CLOSED;
    this.closed = true;
    // Real browsers fire onclose asynchronously; firing it synchronously here is enough
    // for the client's reconnect wiring, which only reads it inside the handler.
    this.onclose?.();
  }

  // --- test drivers ---
  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
}

class FakeBroadcastChannel {
  onmessage: ((event: MessageEvent) => void) | null = null;
  constructor(public name: string) {}
  postMessage() {}
  close() {}
}

const DELTA = { type: "delta", event: { type: "session.state_changed", payload: {} } };

describe("staleness watchdog", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("force-reconnects an OPEN-but-silent socket and reports liveness", () => {
    const liveness: string[] = [];
    const client = new DoorboardEventClient({
      wsUrl: "ws://test/ws",
      staleTimeoutMs: 45000,
      watchdogIntervalMs: 5000,
      onLiveness: (value) => liveness.push(value),
    });

    const first = FakeWebSocket.instances[0];
    first.open();
    expect(liveness.at(-1)).toBe("live");

    // A frame partway through keeps it live and resets the silence clock.
    vi.advanceTimersByTime(20000);
    first.emit(DELTA);
    expect(liveness.at(-1)).toBe("live");
    expect(first.closed).toBe(false);

    // Now stay silent past the window. The watchdog marks stale and force-closes.
    vi.advanceTimersByTime(50000);
    expect(liveness).toContain("stale");
    expect(first.closed).toBe(true);
    // onclose moved us to reconnecting; the backoff then opens a fresh socket.
    expect(liveness.at(-1)).toBe("reconnecting");
    vi.advanceTimersByTime(2000);
    expect(FakeWebSocket.instances.length).toBe(2);

    client.close();
  });

  it("never trips while frames keep arriving inside the window", () => {
    const liveness: string[] = [];
    const client = new DoorboardEventClient({
      wsUrl: "ws://test/ws",
      staleTimeoutMs: 45000,
      watchdogIntervalMs: 5000,
      onLiveness: (value) => liveness.push(value),
    });

    const ws = FakeWebSocket.instances[0];
    ws.open();

    for (let i = 0; i < 10; i += 1) {
      vi.advanceTimersByTime(30000); // below the 45s window
      ws.emit(DELTA);
    }

    expect(ws.closed).toBe(false);
    expect(FakeWebSocket.instances.length).toBe(1);
    expect(liveness).not.toContain("stale");
    expect(liveness.at(-1)).toBe("live");

    client.close();
  });

  it("stops the watchdog after close so no stale reconnect fires", () => {
    const client = new DoorboardEventClient({
      wsUrl: "ws://test/ws",
      staleTimeoutMs: 45000,
      watchdogIntervalMs: 5000,
    });
    const ws = FakeWebSocket.instances[0];
    ws.open();
    client.close();

    // Long after close, with no traffic, nothing should reconnect.
    vi.advanceTimersByTime(600000);
    expect(FakeWebSocket.instances.length).toBe(1);
  });
});
