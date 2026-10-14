// @vitest-environment jsdom
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { FakeWebSocket, sessionDelta } from "./test/fakeWebSocket";

/**
 * Smaller doorpad behaviours that each had a report against them: the tile toasts, the
 * read-only auto-reset ending the session, a visitor token per transition, the recording
 * cap's double stop, the missing connection indicator, and a QR screen that said
 * "preparing" forever after a failed mint.
 */

type Session = Record<string, unknown>;

function mockDoorApi(options: { session?: Session; visitorTokenStatus?: number; maxRecordingS?: number } = {}) {
  const fetchMock = vi.fn<(...args: unknown[]) => Promise<Response>>(async (...args) => {
    const url = String(args[0] ?? "");
    let status = 200;
    let body: unknown = {};
    if (url.includes("/doorpad/activity")) {
      return { ok: true, status: 202, json: async () => ({}) } as Response;
    }
    if (url.endsWith("/session")) {
      body = {
        session: options.session ?? { state: "IDLE", session_id: null },
        config: { max_recording_s: options.maxRecordingS ?? 60 },
      };
    } else if (url.includes("/visitor-token")) {
      status = options.visitorTokenStatus ?? 200;
      body =
        status < 400
          ? { token: "tok-1", url: "http://door.local/visitor?token=tok-1" }
          : { detail: "No active visitor session" };
    } else if (url.includes("/doorpad/video-message/stop")) {
      body = { accepted: true, session: { state: "VIDEO_MESSAGE_REVIEW", session_id: "ses-rec" } };
    } else if (url.includes("/doorpad/video-message/latest")) {
      body = { recording: null };
    } else if (url.includes("/doorpad/session/end")) {
      body = { accepted: true, session: { state: "IDLE", session_id: null, display_name: null } };
    } else if (url.includes("/polls/current")) {
      body = { poll: null };
    }
    return { ok: status < 400, status, json: async () => body } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function calls(fetchMock: ReturnType<typeof mockDoorApi>, match: string): number {
  return fetchMock.mock.calls.filter(([url]) => String(url).includes(match)).length;
}

const HELD_TIGER: Session = {
  state: "IDLE",
  session_id: null,
  display_name: "Tiger",
  profile_id: "sunrise",
  attributed_to: "Tiger",
  identity_expires_in_s: 90,
};

beforeEach(() => {
  window.history.pushState(null, "", "/doorpad");
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.history.pushState(null, "", "/");
});

describe("doorpad polish", () => {
  it("does not toast when a tile is opened", async () => {
    mockDoorApi();
    render(<App />);
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());

    fireEvent.click(screen.getByText("Guestbook"));
    expect(screen.getByText("Leave a Guestbook Note")).toBeTruthy();
    expect(document.querySelector(".db-toast-message")).toBeNull();
  });

  it("returns home from a read-only screen after 30 s without ending the session", async () => {
    const fetchMock = mockDoorApi({ session: HELD_TIGER });
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("doorpad-identity")).toBeTruthy());

    vi.useFakeTimers();
    fireEvent.click(document.getElementById("btn-poll") as HTMLElement);
    expect(screen.getByText("Vote in Poll", { selector: "h2" })).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(31_000);
    });
    vi.useRealTimers();

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    // No /doorpad/session/end: that call also forgets the recognised identity (ADR-0020).
    expect(calls(fetchMock, "/doorpad/session/end")).toBe(0);
    expect(screen.getByTestId("doorpad-identity").textContent).toContain("Tiger");
  });

  it("mints one visitor token per session, not per transition", async () => {
    const fetchMock = mockDoorApi();
    render(<App />);
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    expect(calls(fetchMock, "/visitor-token")).toBe(0);

    act(() => ws.emit(sessionDelta("IDLE", "RINGING", { session_id: "ses-1" })));
    await waitFor(() => expect(calls(fetchMock, "/visitor-token")).toBe(1));
    act(() => ws.emit(sessionDelta("RINGING", "UNANSWERED_TIMEOUT", { session_id: "ses-1" })));
    act(() =>
      ws.emit(sessionDelta("UNANSWERED_TIMEOUT", "VIDEO_MESSAGE_OFFERED", { session_id: "ses-1" }))
    );
    await waitFor(() => expect(screen.getByText("Leave a Video Message")).toBeTruthy());
    expect(calls(fetchMock, "/visitor-token")).toBe(1);

    // A new session mints afresh.
    act(() => ws.emit(sessionDelta("VIDEO_MESSAGE_OFFERED", "SESSION_END", { session_id: "ses-1" })));
    act(() => ws.emit(sessionDelta("SESSION_END", "IDLE", { session_id: "ses-1" })));
    act(() => ws.emit(sessionDelta("IDLE", "RINGING", { session_id: "ses-2" })));
    await waitFor(() => expect(calls(fetchMock, "/visitor-token")).toBe(2));
  });

  it("says the phone link is unavailable after a failed mint, instead of preparing forever", async () => {
    mockDoorApi({ visitorTokenStatus: 503 });
    render(<App />);
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());

    fireEvent.click(screen.getByText("Video Message"));
    fireEvent.click(screen.getByText("Open Visitor QR"));

    await waitFor(() => expect(screen.getByTestId("visitor-link-unavailable")).toBeTruthy());
    expect(screen.queryByText("Preparing QR token...")).toBeNull();
    expect(screen.getByText("Visitor Link")).toBeTruthy();
  });

  it("stops exactly once when the recording reaches its cap", async () => {
    const fetchMock = mockDoorApi({
      session: { state: "VIDEO_MESSAGE_RECORDING", session_id: "ses-rec" },
      maxRecordingS: 1,
    });
    render(<App />);
    await waitFor(() => expect(screen.getByText("Stop & Review")).toBeTruthy());

    await waitFor(() => expect(screen.getByText("Review Message")).toBeTruthy(), {
      timeout: 4_000,
    });
    // Give any second request its chance to land, then count.
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(calls(fetchMock, "/doorpad/video-message/stop")).toBe(1);
  });

  it("shows a connection dot in the header", async () => {
    mockDoorApi();
    render(<App />);
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());

    const dot = screen.getByTestId("connection-dot");
    expect(dot.closest(".doorpad-header")).toBeTruthy();
    // Nothing has arrived on the socket yet, so it is honest about that.
    expect(dot.getAttribute("data-liveness")).toBe("reconnecting");

    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    expect(screen.getByTestId("connection-dot").getAttribute("data-liveness")).toBe("live");
  });
});
