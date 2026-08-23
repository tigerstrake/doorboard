import type { DoorboardEvent } from "@doorboard/contracts";

export { uuidv7 } from "./uuid";


/**
 * Liveness for the UI, distinct from the raw socket status:
 * - "live": frames are arriving.
 * - "reconnecting": the socket is down or connecting (backoff owns recovery).
 * - "stale": the socket is OPEN but has been silent past `staleTimeoutMs`. door-api
 *   drops a stuttering kiosk without closing its socket, so no `onclose` ever fires and
 *   a public display can sit on a frozen frame forever. We surface "stale" and then
 *   force-reconnect so the client recovers on its own.
 */
export type ConnectionLiveness = "live" | "reconnecting" | "stale";

export interface EventClientOptions {
  wsUrl?: string;
  mock?: boolean;
  filters?: string[];
  onStatusChange?: (status: "connecting" | "connected" | "disconnected") => void;
  onSnapshot?: (state: unknown) => void;
  /** Liveness callback (see {@link ConnectionLiveness}). Fires only when the value changes. */
  onLiveness?: (liveness: ConnectionLiveness) => void;
  /**
   * Silence, in ms, after which an OPEN-but-silent socket is treated as a dropped client
   * and force-reconnected. door-api sends session snapshots and ambient replays, so real
   * silence this long means the server dropped us while the socket stayed open. Default 45s.
   */
  staleTimeoutMs?: number;
  /** How often the staleness watchdog checks for silence. Default 5s. Keep it cheap. */
  watchdogIntervalMs?: number;
}

export class DoorboardEventClient {
  private ws: WebSocket | null = null;
  private channel: BroadcastChannel;
  private listeners: Set<{ pattern: string; callback: (event: DoorboardEvent) => void }> = new Set();
  private wsUrl: string;
  private mock: boolean;
  private filters: string[];
  private onStatusChange?: (status: "connecting" | "connected" | "disconnected") => void;
  private onSnapshot?: (state: unknown) => void;
  private onLiveness?: (liveness: ConnectionLiveness) => void;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelay = 1000;
  private maxReconnectDelay = 16000;
  private explicitClose = false;
  // Staleness watchdog: the last time any server frame arrived, plus the timer that
  // checks whether an OPEN socket has gone silent past the window (see ConnectionLiveness).
  private staleTimeoutMs: number;
  private watchdogIntervalMs: number;
  private lastMessageAt = 0;
  private liveness: ConnectionLiveness = "reconnecting";
  private watchdogTimer: ReturnType<typeof setInterval> | null = null;

  constructor(options: EventClientOptions = {}) {
    const defaultWsUrl = `ws://${window.location.hostname}:8765/ws`;
    this.wsUrl = options.wsUrl || defaultWsUrl;
    this.mock = options.mock ?? false;
    this.filters = options.filters || ["*"];
    this.onStatusChange = options.onStatusChange;
    this.onSnapshot = options.onSnapshot;
    this.onLiveness = options.onLiveness;
    this.staleTimeoutMs = options.staleTimeoutMs ?? 45000;
    this.watchdogIntervalMs = options.watchdogIntervalMs ?? 5000;
    this.channel = new BroadcastChannel("doorboard-events");

    // Listen to local BroadcastChannel (for multi-tab sync under mock or fallback)
    this.channel.onmessage = (event: MessageEvent) => {
      const dbEvent = event.data as DoorboardEvent;
      if (dbEvent && dbEvent.type) {
        this.notifyListeners(dbEvent);
      }
    };

    if (!this.mock) {
      this.connect();
      this.startWatchdog();
    }
  }

  private setLiveness(next: ConnectionLiveness) {
    if (this.liveness === next) return;
    this.liveness = next;
    this.onLiveness?.(next);
  }

  private connect() {
    if (this.explicitClose) return;

    this.onStatusChange?.("connecting");
    try {
      this.ws = new WebSocket(this.wsUrl);

      this.ws.onopen = () => {
        this.onStatusChange?.("connected");
        this.reconnectDelay = 1000; // Reset delay on success
        this.lastMessageAt = Date.now();
        this.setLiveness("live");

        // Subscribe with filters per conventions
        this.ws?.send(JSON.stringify({ subscribe: this.filters }));
      };

      this.ws.onmessage = (messageEvent) => {
        // Any server frame proves the socket is alive — subscribe acks and frames we do
        // not otherwise handle included. The watchdog cares that the socket is silent,
        // not which message type broke the silence.
        this.lastMessageAt = Date.now();
        this.setLiveness("live");
        try {
          const raw = JSON.parse(messageEvent.data) as unknown;
          if (isSnapshotMessage(raw)) {
            this.onSnapshot?.(raw.state);
            return;
          }
          const dbEvent = isDeltaMessage(raw) ? raw.event : raw;
          if (isDoorboardEvent(dbEvent)) {
            this.notifyListeners(dbEvent);
            // Also mirror to BroadcastChannel so other tabs sync up
            this.channel.postMessage(dbEvent);
          }
        } catch (err) {
          console.error("Failed to parse WebSocket event:", err);
        }
      };

      this.ws.onclose = () => {
        this.onStatusChange?.("disconnected");
        this.ws = null;
        this.setLiveness("reconnecting");
        this.scheduleReconnect();
      };

      this.ws.onerror = () => {
        this.ws?.close();
      };
    } catch (err) {
      console.error("Error setting up WebSocket:", err);
      this.onStatusChange?.("disconnected");
      this.setLiveness("reconnecting");
      this.scheduleReconnect();
    }
  }

  private startWatchdog() {
    if (this.watchdogTimer !== null) return;
    this.watchdogTimer = setInterval(() => this.checkLiveness(), this.watchdogIntervalMs);
  }

  private stopWatchdog() {
    if (this.watchdogTimer !== null) {
      clearInterval(this.watchdogTimer);
      this.watchdogTimer = null;
    }
  }

  private checkLiveness() {
    if (this.explicitClose) return;
    // A socket that isn't OPEN is already the reconnect path's business: onclose fired and
    // scheduled a backoff. The watchdog exists only for the socket that stays OPEN while
    // the server has quietly stopped sending — no onclose ever fires, so nothing else notices.
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    if (Date.now() - this.lastMessageAt <= this.staleTimeoutMs) return;
    // Silent past the window: surface "stale" for the UI, then force the socket closed so the
    // existing exponential-backoff reconnect takes over and a dropped-but-open client recovers
    // on its own instead of showing a frozen frame forever. onclose moves us to "reconnecting".
    this.setLiveness("stale");
    try {
      this.ws.close();
    } catch {
      this.ws = null;
      this.setLiveness("reconnecting");
      this.scheduleReconnect();
    }
  }

  private scheduleReconnect() {
    if (this.explicitClose) return;
    if (this.reconnectTimeout) return;

    this.reconnectTimeout = setTimeout(() => {
      this.reconnectTimeout = null;
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay);
      this.connect();
    }, this.reconnectDelay);
  }

  private notifyListeners(event: DoorboardEvent) {
    this.listeners.forEach(({ pattern, callback }) => {
      if (this.matchesPattern(event.type, pattern)) {
        try {
          callback(event);
        } catch (err) {
          console.error("Error in event subscriber:", err);
        }
      }
    });
  }

  private matchesPattern(type: string, pattern: string): boolean {
    if (pattern === "*" || pattern === "") return true;
    if (pattern.endsWith(".*")) {
      const prefix = pattern.slice(0, -2);
      return type.startsWith(prefix);
    }
    return type === pattern;
  }

  public subscribe(pattern: string, callback: (event: DoorboardEvent) => void): () => void {
    const listener = { pattern, callback };
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  public publish(event: DoorboardEvent): void {
    // Send over BroadcastChannel to other tabs
    this.channel.postMessage(event);

    // Notify our local listeners
    this.notifyListeners(event);

    // If WS is open and we aren't purely mock, push it to backend
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify(event));
      } catch (err) {
        console.error("Failed to send event over WebSocket:", err);
      }
    }
  }

  public close() {
    this.explicitClose = true;
    this.stopWatchdog();
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.channel.close();
  }
}


function isDoorboardEvent(value: unknown): value is DoorboardEvent {
  return Boolean(
    value &&
      typeof value === "object" &&
      "type" in value &&
      typeof (value as { type?: unknown }).type === "string"
  );
}

function isDeltaMessage(value: unknown): value is { type: "delta"; event: DoorboardEvent } {
  return Boolean(
    value &&
      typeof value === "object" &&
      (value as { type?: unknown }).type === "delta" &&
      isDoorboardEvent((value as { event?: unknown }).event)
  );
}

function isSnapshotMessage(value: unknown): value is { type: "snapshot"; state: unknown } {
  return Boolean(
    value &&
      typeof value === "object" &&
      (value as { type?: unknown }).type === "snapshot" &&
      "state" in value
  );
}
