import type { DoorboardEvent } from "@doorboard/contracts";

export { uuidv7 } from "./uuid";


export interface EventClientOptions {
  wsUrl?: string;
  mock?: boolean;
  filters?: string[];
  onStatusChange?: (status: "connecting" | "connected" | "disconnected") => void;
}

export class DoorboardEventClient {
  private ws: WebSocket | null = null;
  private channel: BroadcastChannel;
  private listeners: Set<{ pattern: string; callback: (event: DoorboardEvent) => void }> = new Set();
  private wsUrl: string;
  private mock: boolean;
  private filters: string[];
  private onStatusChange?: (status: "connecting" | "connected" | "disconnected") => void;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelay = 1000;
  private maxReconnectDelay = 16000;
  private explicitClose = false;

  constructor(options: EventClientOptions = {}) {
    const defaultWsUrl = `ws://${window.location.hostname}:8765/ws`;
    this.wsUrl = options.wsUrl || defaultWsUrl;
    this.mock = options.mock ?? false;
    this.filters = options.filters || ["*"];
    this.onStatusChange = options.onStatusChange;
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
    }
  }

  private connect() {
    if (this.explicitClose) return;

    this.onStatusChange?.("connecting");
    try {
      this.ws = new WebSocket(this.wsUrl);

      this.ws.onopen = () => {
        this.onStatusChange?.("connected");
        this.reconnectDelay = 1000; // Reset delay on success
        
        // Subscribe with filters per conventions
        this.ws?.send(JSON.stringify({ subscribe: this.filters }));
      };

      this.ws.onmessage = (messageEvent) => {
        try {
          const dbEvent = JSON.parse(messageEvent.data) as DoorboardEvent;
          if (dbEvent && dbEvent.type) {
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
        this.scheduleReconnect();
      };

      this.ws.onerror = () => {
        this.ws?.close();
      };
    } catch (err) {
      console.error("Error setting up WebSocket:", err);
      this.onStatusChange?.("disconnected");
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
