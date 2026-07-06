export type StreamHealth = "connecting" | "connected" | "unavailable";

export interface StreamMetadata {
  name: string;
  whep_url: string;
  stream_up: boolean;
  webrtc_clients: number;
}

export interface WhepPlayerSnapshot {
  status: StreamHealth;
  stream: MediaStream | null;
  streamName: string;
  lastError: string | null;
  connectedAtMonotonicMs: number | null;
}

export interface WhepPlayerOptions {
  metadataUrl: string;
  streamName?: string;
  fetchTimeoutMs?: number;
  healthPollMs?: number;
  reconnectMinMs?: number;
  reconnectMaxMs?: number;
  onChange?: (snapshot: WhepPlayerSnapshot) => void;
  fetchImpl?: typeof fetch;
  peerConnectionFactory?: () => RTCPeerConnection;
}

interface MockStreamHandle {
  stream: MediaStream;
  stop: () => void;
}

const DEFAULT_STREAM_NAME = "visitor";
const DEFAULT_FETCH_TIMEOUT_MS = 3000;
const DEFAULT_HEALTH_POLL_MS = 1000;
const DEFAULT_RECONNECT_MIN_MS = 500;
const DEFAULT_RECONNECT_MAX_MS = 5000;

let activePeerConnections = 0;
let createdPeerConnections = 0;
let closedPeerConnections = 0;

export function getMediaClientStats() {
  return {
    activePeerConnections,
    createdPeerConnections,
    closedPeerConnections,
  };
}

export async function fetchStreamMetadata(
  metadataUrl: string,
  options: {
    fetchImpl?: typeof fetch;
    timeoutMs?: number;
    signal?: AbortSignal;
  } = {},
): Promise<StreamMetadata[]> {
  const fetcher = options.fetchImpl ?? fetch;
  const timeoutMs = options.timeoutMs ?? DEFAULT_FETCH_TIMEOUT_MS;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  const signal = mergeAbortSignals(controller.signal, options.signal);

  try {
    const response = await fetcher(metadataUrl, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal,
    });
    if (!response.ok) {
      throw new Error(`stream metadata request failed: ${response.status}`);
    }
    const json: unknown = await response.json();
    return parseStreamMetadata(json);
  } finally {
    window.clearTimeout(timeout);
  }
}

export class WhepPlayer {
  private readonly options: Required<
    Pick<
      WhepPlayerOptions,
      "metadataUrl" | "streamName" | "fetchTimeoutMs" | "healthPollMs" | "reconnectMinMs" | "reconnectMaxMs"
    >
  > &
    Pick<WhepPlayerOptions, "onChange" | "fetchImpl" | "peerConnectionFactory">;

  private snapshot: WhepPlayerSnapshot;
  private stopped = true;
  private reconnectDelayMs: number;
  private generation = 0;
  private retryTimer: number | null = null;
  private healthTimer: number | null = null;
  private abortController: AbortController | null = null;
  private peerConnection: RTCPeerConnection | null = null;
  private whepResourceUrl: string | null = null;
  private mockHandle: MockStreamHandle | null = null;

  constructor(options: WhepPlayerOptions) {
    this.options = {
      metadataUrl: options.metadataUrl,
      streamName: options.streamName ?? DEFAULT_STREAM_NAME,
      fetchTimeoutMs: options.fetchTimeoutMs ?? DEFAULT_FETCH_TIMEOUT_MS,
      healthPollMs: options.healthPollMs ?? DEFAULT_HEALTH_POLL_MS,
      reconnectMinMs: options.reconnectMinMs ?? DEFAULT_RECONNECT_MIN_MS,
      reconnectMaxMs: options.reconnectMaxMs ?? DEFAULT_RECONNECT_MAX_MS,
      onChange: options.onChange,
      fetchImpl: options.fetchImpl,
      peerConnectionFactory: options.peerConnectionFactory,
    };
    this.reconnectDelayMs = this.options.reconnectMinMs;
    this.snapshot = {
      status: "connecting",
      stream: null,
      streamName: this.options.streamName,
      lastError: null,
      connectedAtMonotonicMs: null,
    };
  }

  start(): void {
    if (!this.stopped) {
      return;
    }
    this.stopped = false;
    this.reconnectDelayMs = this.options.reconnectMinMs;
    this.transition({ status: "connecting", lastError: null });
    this.connectSoon(0);
  }

  stop(): void {
    this.stopped = true;
    this.generation += 1;
    this.clearTimers();
    this.abortController?.abort();
    this.abortController = null;
    void this.closePeerConnection();
    this.stopMockStream();
    this.stopMediaStream(this.snapshot.stream);
    this.transition({
      status: "unavailable",
      stream: null,
      connectedAtMonotonicMs: null,
    });
  }

  getSnapshot(): WhepPlayerSnapshot {
    return this.snapshot;
  }

  private connectSoon(delayMs: number): void {
    if (this.stopped) {
      return;
    }
    if (this.retryTimer !== null) {
      window.clearTimeout(this.retryTimer);
    }
    this.retryTimer = window.setTimeout(() => {
      this.retryTimer = null;
      void this.connect(this.generation);
    }, delayMs);
  }

  private async connect(generation: number): Promise<void> {
    if (this.stopped || generation !== this.generation) {
      return;
    }

    this.abortController?.abort();
    this.abortController = new AbortController();

    try {
      this.transition({ status: "connecting", lastError: null });
      const stream = await this.resolveStreamMetadata(this.abortController.signal);
      if (!stream.stream_up) {
        throw new Error(`stream ${stream.name} is unavailable`);
      }

      if (isMockWhepUrl(stream.whep_url)) {
        await this.connectMock(stream);
      } else {
        await this.connectWhep(stream);
      }

      if (this.stopped || generation !== this.generation) {
        return;
      }

      this.reconnectDelayMs = this.options.reconnectMinMs;
      this.transition({
        status: "connected",
        lastError: null,
        connectedAtMonotonicMs: performance.now(),
      });
      this.startHealthPolling(generation);
    } catch (error) {
      if (this.stopped || generation !== this.generation) {
        return;
      }
      await this.markUnavailable(error);
      const delay = this.reconnectDelayMs;
      this.reconnectDelayMs = Math.min(
        this.reconnectDelayMs * 2,
        this.options.reconnectMaxMs,
      );
      this.connectSoon(delay);
    }
  }

  private async resolveStreamMetadata(signal: AbortSignal): Promise<StreamMetadata> {
    const streams = await fetchStreamMetadata(this.options.metadataUrl, {
      fetchImpl: this.options.fetchImpl,
      timeoutMs: this.options.fetchTimeoutMs,
      signal,
    });
    const stream = streams.find((entry) => entry.name === this.options.streamName);
    if (!stream) {
      throw new Error(`stream ${this.options.streamName} not found`);
    }
    return stream;
  }

  private async connectMock(stream: StreamMetadata): Promise<void> {
    await this.closePeerConnection();
    this.stopMockStream();
    this.stopMediaStream(this.snapshot.stream);
    const handle = createMockStream(stream.name);
    this.mockHandle = handle;
    this.transition({ stream: handle.stream });
  }

  private async connectWhep(stream: StreamMetadata): Promise<void> {
    await this.closePeerConnection();
    this.stopMockStream();
    this.stopMediaStream(this.snapshot.stream);

    const pc =
      this.options.peerConnectionFactory?.() ??
      new RTCPeerConnection({ bundlePolicy: "max-bundle" });
    this.peerConnection = pc;
    activePeerConnections += 1;
    createdPeerConnections += 1;

    const mediaStream = new MediaStream();
    this.transition({ stream: mediaStream });

    pc.addTransceiver("video", { direction: "recvonly" });
    pc.addTransceiver("audio", { direction: "recvonly" });
    pc.ontrack = (event) => {
      for (const track of event.streams[0]?.getTracks() ?? [event.track]) {
        if (!mediaStream.getTracks().some((existing) => existing.id === track.id)) {
          mediaStream.addTrack(track);
        }
      }
      this.transition({ stream: mediaStream });
    };
    pc.onconnectionstatechange = () => {
      if (
        pc.connectionState === "failed" ||
        pc.connectionState === "disconnected" ||
        pc.connectionState === "closed"
      ) {
        void this.markUnavailable(new Error(`peer connection ${pc.connectionState}`));
        this.connectSoon(this.options.reconnectMinMs);
      }
    };

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const localDescription = pc.localDescription;
    if (!localDescription?.sdp) {
      throw new Error("failed to create WHEP offer");
    }

    const response = await (this.options.fetchImpl ?? fetch)(resolveWhepUrl(stream.whep_url), {
      method: "POST",
      headers: {
        Accept: "application/sdp",
        "Content-Type": "application/sdp",
      },
      body: localDescription.sdp,
      signal: this.abortController?.signal,
    });

    if (response.status !== 200 && response.status !== 201) {
      throw new Error(`WHEP offer rejected: ${response.status}`);
    }

    const answerSdp = await response.text();
    this.whepResourceUrl = response.headers.get("Location");
    await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
  }

  private startHealthPolling(generation: number): void {
    if (this.healthTimer !== null) {
      window.clearInterval(this.healthTimer);
    }
    this.healthTimer = window.setInterval(() => {
      void this.pollHealth(generation);
    }, this.options.healthPollMs);
  }

  private async pollHealth(generation: number): Promise<void> {
    if (this.stopped || generation !== this.generation) {
      return;
    }
    try {
      const stream = await this.resolveStreamMetadata(new AbortController().signal);
      if (!stream.stream_up) {
        throw new Error(`stream ${stream.name} is unavailable`);
      }
    } catch (error) {
      if (this.stopped || generation !== this.generation) {
        return;
      }
      await this.markUnavailable(error);
      this.connectSoon(this.options.reconnectMinMs);
    }
  }

  private async markUnavailable(error: unknown): Promise<void> {
    this.clearHealthTimer();
    this.abortController?.abort();
    this.abortController = null;
    await this.closePeerConnection();
    this.stopMockStream();
    this.stopMediaStream(this.snapshot.stream);
    this.transition({
      status: "unavailable",
      stream: null,
      lastError: errorMessage(error),
      connectedAtMonotonicMs: null,
    });
  }

  private async closePeerConnection(): Promise<void> {
    const pc = this.peerConnection;
    this.peerConnection = null;
    if (this.whepResourceUrl) {
      const url = this.whepResourceUrl;
      this.whepResourceUrl = null;
      try {
        await (this.options.fetchImpl ?? fetch)(url, { method: "DELETE", keepalive: true });
      } catch {
        // Best-effort WHEP resource cleanup; the PeerConnection is closed below.
      }
    }
    if (pc) {
      pc.ontrack = null;
      pc.onconnectionstatechange = null;
      pc.close();
      activePeerConnections = Math.max(0, activePeerConnections - 1);
      closedPeerConnections += 1;
    }
  }

  private stopMockStream(): void {
    this.mockHandle?.stop();
    this.mockHandle = null;
  }

  private stopMediaStream(stream: MediaStream | null): void {
    for (const track of stream?.getTracks() ?? []) {
      track.stop();
    }
  }

  private clearTimers(): void {
    if (this.retryTimer !== null) {
      window.clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    this.clearHealthTimer();
  }

  private clearHealthTimer(): void {
    if (this.healthTimer !== null) {
      window.clearInterval(this.healthTimer);
      this.healthTimer = null;
    }
  }

  private transition(next: Partial<WhepPlayerSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...next };
    this.options.onChange?.(this.snapshot);
  }
}

export function createWhepPlayer(options: WhepPlayerOptions): WhepPlayer {
  return new WhepPlayer(options);
}

function parseStreamMetadata(json: unknown): StreamMetadata[] {
  if (!Array.isArray(json)) {
    throw new Error("stream metadata payload must be a list");
  }
  return json.map((entry) => {
    if (!isRecord(entry)) {
      throw new Error("stream metadata entry must be an object");
    }
    const name = entry.name;
    const whepUrl = entry.whep_url;
    const streamUp = entry.stream_up;
    const webrtcClients = entry.webrtc_clients;
    if (
      typeof name !== "string" ||
      typeof whepUrl !== "string" ||
      typeof streamUp !== "boolean" ||
      typeof webrtcClients !== "number" ||
      !Number.isFinite(webrtcClients) ||
      webrtcClients < 0
    ) {
      throw new Error("stream metadata entry has invalid fields");
    }
    return {
      name,
      whep_url: whepUrl,
      stream_up: streamUp,
      webrtc_clients: webrtcClients,
    };
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isMockWhepUrl(url: string): boolean {
  return url.startsWith("mock:") || url.includes("/mock/whep/");
}

function resolveWhepUrl(url: string): string {
  return new URL(url, window.location.href).toString();
}

function mergeAbortSignals(first: AbortSignal, second?: AbortSignal): AbortSignal {
  if (!second) {
    return first;
  }
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (first.aborted || second.aborted) {
    abort();
  } else {
    first.addEventListener("abort", abort, { once: true });
    second.addEventListener("abort", abort, { once: true });
  }
  return controller.signal;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function createMockStream(name: string): MockStreamHandle {
  const canvas = document.createElement("canvas");
  canvas.width = 640;
  canvas.height = 360;
  const context = canvas.getContext("2d");
  if (!context || typeof canvas.captureStream !== "function") {
    return {
      stream: new MediaStream(),
      stop: () => undefined,
    };
  }

  let frame = 0;
  const draw = () => {
    const width = canvas.width;
    const height = canvas.height;
    context.fillStyle = "#0f172a";
    context.fillRect(0, 0, width, height);
    context.fillStyle = "#14b8a6";
    context.fillRect(0, 0, width, 36);
    context.fillStyle = "#f97316";
    context.fillRect((frame * 9) % width, 80, 120, 80);
    context.fillStyle = "#f8fafc";
    context.font = "24px sans-serif";
    context.fillText(`door-media mock: ${name}`, 24, 26);
    context.font = "56px monospace";
    context.fillText(new Date().toISOString().slice(11, 19), 140, 210);
    frame += 1;
  };
  draw();
  const interval = window.setInterval(draw, 250);
  const stream = canvas.captureStream(10);
  return {
    stream,
    stop: () => {
      window.clearInterval(interval);
      for (const track of stream.getTracks()) {
        track.stop();
      }
    },
  };
}
