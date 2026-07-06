// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { WhepPlayer, fetchStreamMetadata } from "./index";

function jsonResponse(body: unknown, init: { ok?: boolean; status?: number } = {}): Response {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => body,
  } as unknown as Response;
}

class FakeMediaStream {
  private tracks: MediaStreamTrack[] = [];
  addTrack(track: MediaStreamTrack): void {
    this.tracks.push(track);
  }
  getTracks(): MediaStreamTrack[] {
    return this.tracks;
  }
}
(globalThis as { MediaStream?: unknown }).MediaStream ??= FakeMediaStream;

function fakePeerConnection(onClose: () => void) {
  const pc = {
    ontrack: null as ((event: RTCTrackEvent) => void) | null,
    onconnectionstatechange: null as (() => void) | null,
    connectionState: "new" as RTCPeerConnectionState,
    localDescription: null as RTCSessionDescription | null,
    addTransceiver: vi.fn(),
    createOffer: vi.fn(async () => ({ type: "offer" as const, sdp: "fake-offer-sdp" })),
    setLocalDescription: vi.fn(async (desc: RTCSessionDescriptionInit) => {
      pc.localDescription = { type: desc.type, sdp: desc.sdp } as RTCSessionDescription;
    }),
    setRemoteDescription: vi.fn(async () => undefined),
    close: vi.fn(() => onClose()),
  };
  return pc;
}

type FakePeerConnection = ReturnType<typeof fakePeerConnection>;

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("WhepPlayer reconnect backoff", () => {
  it("doubles the reconnect delay from the minimum up to the cap", async () => {
    const fetchImpl: typeof fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const connectingAt: number[] = [];
    const player = new WhepPlayer({
      metadataUrl: "https://media.local/streams",
      fetchImpl,
      reconnectMinMs: 100,
      reconnectMaxMs: 1000,
      healthPollMs: 1_000_000,
      onChange: (snapshot) => {
        if (snapshot.status === "connecting") {
          const now = Date.now();
          if (connectingAt[connectingAt.length - 1] !== now) {
            connectingAt.push(now);
          }
        }
      },
    });

    player.start();
    await vi.advanceTimersByTimeAsync(3600);
    player.stop();

    const delays = connectingAt.slice(1).map((t, i) => t - connectingAt[i]);
    expect(delays.slice(0, 6)).toEqual([100, 200, 400, 800, 1000, 1000]);
  });
});

describe("WhepPlayer generation guard", () => {
  it("ignores a stale connect attempt that resolves after stop()/start()", async () => {
    const resolvers: Array<(value: Response) => void> = [];
    const fetchImpl: typeof fetch = vi.fn(
      () => new Promise<Response>((resolve) => resolvers.push(resolve)),
    );
    const onChange = vi.fn();
    const player = new WhepPlayer({
      metadataUrl: "https://media.local/streams",
      fetchImpl,
      onChange,
      reconnectMinMs: 50,
      reconnectMaxMs: 50,
    });

    player.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(resolvers).toHaveLength(1);

    player.stop();
    player.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(resolvers).toHaveLength(2);

    onChange.mockClear();

    resolvers[0](
      jsonResponse([{ name: "visitor", whep_url: "mock:visitor", stream_up: false, webrtc_clients: 0 }]),
    );
    await vi.advanceTimersByTimeAsync(0);

    expect(onChange).not.toHaveBeenCalled();
    expect(player.getSnapshot().lastError).toBeNull();

    player.stop();
  });
});

describe("WhepPlayer peer connection teardown", () => {
  it("closes the peer connection exactly once per reconnect cycle", async () => {
    let closeCount = 0;
    const createdPcs: FakePeerConnection[] = [];
    const fetchImpl: typeof fetch = vi.fn(async (_input, init) => {
      const method = init?.method ?? "GET";
      if (method === "POST") {
        return {
          ok: true,
          status: 201,
          headers: { get: (name: string) => (name === "Location" ? "https://media.local/whep/visitor/1" : null) },
          text: async () => "fake-answer-sdp",
        } as unknown as Response;
      }
      if (method === "DELETE") {
        return jsonResponse({});
      }
      return jsonResponse([
        { name: "visitor", whep_url: "https://media.local/whep/visitor", stream_up: true, webrtc_clients: 0 },
      ]);
    });

    const player = new WhepPlayer({
      metadataUrl: "https://media.local/streams",
      fetchImpl,
      reconnectMinMs: 50,
      reconnectMaxMs: 50,
      healthPollMs: 1_000_000,
      peerConnectionFactory: () => {
        const pc = fakePeerConnection(() => {
          closeCount += 1;
        });
        createdPcs.push(pc);
        return pc as unknown as RTCPeerConnection;
      },
    });

    player.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(player.getSnapshot().status).toBe("connected");
    expect(closeCount).toBe(0);

    createdPcs[0].connectionState = "failed";
    createdPcs[0].onconnectionstatechange?.();
    await vi.advanceTimersByTimeAsync(50);
    expect(player.getSnapshot().status).toBe("connected");
    expect(closeCount).toBe(1);

    createdPcs[1].connectionState = "failed";
    createdPcs[1].onconnectionstatechange?.();
    await vi.advanceTimersByTimeAsync(50);
    expect(player.getSnapshot().status).toBe("connected");
    expect(closeCount).toBe(2);

    player.stop();
  });
});

describe("parseStreamMetadata validation (via fetchStreamMetadata)", () => {
  it("rejects a non-array payload", async () => {
    const fetchImpl: typeof fetch = vi.fn(async () => jsonResponse({ not: "an array" }));
    await expect(
      fetchStreamMetadata("https://media.local/streams", { fetchImpl }),
    ).rejects.toThrow("stream metadata payload must be a list");
  });

  it("rejects entries missing required fields", async () => {
    const fetchImpl: typeof fetch = vi.fn(async () => jsonResponse([{ name: "visitor" }]));
    await expect(
      fetchStreamMetadata("https://media.local/streams", { fetchImpl }),
    ).rejects.toThrow("stream metadata entry has invalid fields");
  });

  it("rejects entries with the wrong field types", async () => {
    const fetchImpl: typeof fetch = vi.fn(async () =>
      jsonResponse([{ name: "visitor", whep_url: "mock:visitor", stream_up: "yes", webrtc_clients: 0 }]),
    );
    await expect(
      fetchStreamMetadata("https://media.local/streams", { fetchImpl }),
    ).rejects.toThrow("stream metadata entry has invalid fields");
  });

  it("accepts a well-formed payload", async () => {
    const fetchImpl: typeof fetch = vi.fn(async () =>
      jsonResponse([{ name: "visitor", whep_url: "mock:visitor", stream_up: true, webrtc_clients: 2 }]),
    );
    await expect(fetchStreamMetadata("https://media.local/streams", { fetchImpl })).resolves.toEqual([
      { name: "visitor", whep_url: "mock:visitor", stream_up: true, webrtc_clients: 2 },
    ]);
  });
});
