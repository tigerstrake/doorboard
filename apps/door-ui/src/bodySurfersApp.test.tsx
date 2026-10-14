// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

/**
 * Body Surfers on the two kiosks (ADR-0049). What these pin is the critical-path rule:
 * the bell, and privacy mode, always take the wallboard back from the game, and a game
 * never comes back on its own afterwards.
 */

// The event client mirrors /ws onto this BroadcastChannel, so posting here is how a
// test delivers a door-api message to App.
let bus: BroadcastChannel;
const deliver = async (event: Record<string, unknown>) => {
  await act(async () => {
    bus.postMessage(event);
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
};

const game = (action: string, requestId = "r1") => ({
  type: "wallboard.game_changed",
  game: "body_surfers",
  action,
  reason: action === "start" ? "doorpad" : "ended",
  requestId,
  requestedAt: Date.now(),
});

const sessionTo = (to_state: string) => ({
  type: "session.state_changed",
  event_id: "e1",
  source: "door-api",
  payload: { session_id: "s1", from_state: "IDLE", to_state, trigger: "test" },
});

type FetchArgs = [input: unknown, init?: RequestInit];

function mockFetch(onGame?: (body: unknown) => Response) {
  const fetchMock = vi.fn<(...args: FetchArgs) => Promise<Response>>(async (input, init) => {
    const url = String(input ?? "");
    if (url.endsWith("/wallboard/game") && onGame) {
      return onGame(init?.body ? JSON.parse(String(init.body)) : null);
    }
    const body = url.includes("/guestbook")
      ? { entries: [] }
      : url.includes("/polls/current") || url.includes("/poll")
        ? { poll: null }
        : { session: { state: "IDLE" }, config: {} };
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const gameCalls = (fetchMock: ReturnType<typeof mockFetch>) =>
  fetchMock.mock.calls
    .filter(([input]) => String(input).endsWith("/wallboard/game"))
    .map(([, init]) => JSON.parse(String(init?.body)));

beforeEach(() => {
  bus = new BroadcastChannel("doorboard-events");
});

afterEach(() => {
  bus.close();
  cleanup();
  vi.unstubAllGlobals();
});

describe("wallboard", () => {
  beforeEach(() => {
    window.history.pushState(null, "", "/wallboard");
  });

  it("shows the game in a frame from the other loopback name on start", async () => {
    mockFetch();
    render(<App />);
    await deliver(game("start"));

    const frame = await waitFor(() => screen.getByTestId("body-surfers-frame"));
    const src = new URL(frame.querySelector("iframe")!.getAttribute("src")!);
    expect(src.hostname).not.toBe(window.location.hostname);
    expect(src.pathname).toBe("/games/body-surfers/index.html");
    expect(src.searchParams.get("parent")).toBe(window.location.origin);
    expect(frame.querySelector("iframe")!.getAttribute("sandbox")).toBe("allow-scripts allow-same-origin");
  });

  it("drops the game the moment a bell press takes over, and does not bring it back", async () => {
    mockFetch();
    render(<App />);
    await deliver(game("start"));
    await waitFor(() => screen.getByTestId("body-surfers-frame"));

    await deliver(sessionTo("BUTTON_PRESSED"));
    expect(screen.queryByTestId("body-surfers-frame")).toBeNull();

    await deliver(sessionTo("IDLE"));
    expect(screen.queryByTestId("body-surfers-frame")).toBeNull();
  });

  it("ignores a start while a visitor holds the wallboard", async () => {
    mockFetch();
    render(<App />);
    await deliver(sessionTo("RINGING"));
    await deliver(game("start"));
    expect(screen.queryByTestId("body-surfers-frame")).toBeNull();
  });

  it("ends the game when privacy mode switches on", async () => {
    mockFetch();
    render(<App />);
    await deliver(game("start"));
    await waitFor(() => screen.getByTestId("body-surfers-frame"));
    await deliver({
      type: "vision.privacy_mode_changed",
      event_id: "e2",
      source: "door-visiond",
      payload: { enabled: true, changed_by: "admin" },
    });
    expect(screen.queryByTestId("body-surfers-frame")).toBeNull();
  });

  it("closes on the game's own exit and tells door-api, ignoring other senders", async () => {
    const fetchMock = mockFetch(() => ({ ok: true, status: 200, json: async () => ({ ok: true }) }) as Response);
    render(<App />);
    await deliver(game("start"));
    const frame = await waitFor(() => screen.getByTestId("body-surfers-frame"));
    const iframe = frame.querySelector("iframe")!;
    const origin = new URL(iframe.getAttribute("src")!).origin;
    const exit = { channel: "body-surfers", type: "exit", reason: "walked_away" };

    // Right data, wrong sender: another origin, or a window that is not the game's.
    await act(async () => {
      window.dispatchEvent(new MessageEvent("message", { data: exit, origin: "http://evil.example", source: iframe.contentWindow }));
      window.dispatchEvent(new MessageEvent("message", { data: exit, origin, source: window }));
    });
    expect(screen.queryByTestId("body-surfers-frame")).not.toBeNull();

    await act(async () => {
      window.dispatchEvent(new MessageEvent("message", { data: exit, origin, source: iframe.contentWindow }));
    });
    expect(screen.queryByTestId("body-surfers-frame")).toBeNull();
    expect(gameCalls(fetchMock)).toContainEqual({ action: "stop", reason: "ended" });
  });

  it("does not restart a running game on a second start", async () => {
    mockFetch();
    render(<App />);
    await deliver(game("start", "r1"));
    const first = (await waitFor(() => screen.getByTestId("body-surfers-frame"))).querySelector("iframe");
    await deliver(game("start", "r2"));
    expect(screen.getByTestId("body-surfers-frame").querySelector("iframe")).toBe(first);
  });

  it("loads the hidden clear page on an owner clear request", async () => {
    mockFetch();
    render(<App />);
    await deliver(game("clear_leaderboard"));
    const clear = await waitFor(() => screen.getByTestId("body-surfers-clear"));
    expect(new URL(clear.getAttribute("src")!).pathname).toBe("/games/body-surfers/clear.html");
    expect(screen.queryByTestId("body-surfers-frame")).toBeNull();
  });
});

describe("doorpad", () => {
  beforeEach(() => {
    window.history.pushState(null, "", "/doorpad");
  });

  it("starts the game from its tile and offers Stop", async () => {
    const fetchMock = mockFetch(() => ({ ok: true, status: 200, json: async () => ({ ok: true }) }) as Response);
    render(<App />);
    fireEvent.click(await waitFor(() => screen.getByText("Body Surfers")));

    await waitFor(() => screen.getByTestId("doorpad-body-surfers"));
    expect(gameCalls(fetchMock)).toContainEqual({ action: "start", reason: "doorpad" });
    expect(screen.getByText(/takes a photo of your face/)).toBeTruthy();

    fireEvent.click(screen.getByText("Stop game"));
    expect(gameCalls(fetchMock)).toContainEqual({ action: "stop", reason: "doorpad" });
    expect(screen.queryByTestId("doorpad-body-surfers")).toBeNull();
  });

  it("explains a refused start", async () => {
    mockFetch(
      () => ({ ok: false, status: 409, json: async () => ({ detail: "privacy_mode" }) }) as Response,
    );
    render(<App />);
    fireEvent.click(await waitFor(() => screen.getByText("Body Surfers")));
    await waitFor(() => screen.getByText(/camera is off right now/));
    expect(screen.queryByText("Stop game")).toBeNull();
  });

  it("returns home when the game ends on the wallboard", async () => {
    mockFetch(() => ({ ok: true, status: 200, json: async () => ({ ok: true }) }) as Response);
    render(<App />);
    fireEvent.click(await waitFor(() => screen.getByText("Body Surfers")));
    await waitFor(() => screen.getByTestId("doorpad-body-surfers"));
    await deliver(game("start"));
    await deliver(game("stop"));
    await waitFor(() => expect(screen.queryByTestId("doorpad-body-surfers")).toBeNull());
  });
});
