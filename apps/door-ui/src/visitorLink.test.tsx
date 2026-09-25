// @vitest-environment jsdom
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { FakeWebSocket, sessionDelta } from "./test/fakeWebSocket";

/**
 * The "scan to write on your phone" screen.
 *
 * Reported sequence: ring → Open Visitor QR → scan → typing → at ring+30 s door-api's
 * UNANSWERED_TIMEOUT delta routed the pad back to the ringing screen (QR gone) → +3 s the
 * offer → 30 s idle → the discard path ended the session → the phone got the dead-end
 * page mid-sentence. The screen is sticky now: while the link is displayed, a server
 * transition leaves it alone, and only the session ending (or the link's own ten-minute
 * expiry) moves the visitor on.
 */

function mockDoorApi() {
  const fetchMock = vi.fn<(...args: unknown[]) => Promise<Response>>(async (...args) => {
    const url = String(args[0] ?? "");
    let body: unknown = {};
    if (url.includes("/doorpad/activity")) {
      return { ok: true, status: 202, json: async () => ({}) } as Response;
    }
    if (url.endsWith("/session")) {
      body = { session: { state: "IDLE", session_id: null }, config: {} };
    } else if (url.includes("/doorpad/ring")) {
      body = { accepted: true, session: { state: "RINGING", session_id: "ses-ring" } };
    } else if (url.includes("/visitor-token")) {
      body = { token: "tok-ring", url: "http://door.local/visitor?token=tok-ring" };
    }
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  window.history.pushState(null, "", "/doorpad");
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.pushState(null, "", "/");
});

describe("the visitor-link screen is sticky", () => {
  it("survives UNANSWERED_TIMEOUT and VIDEO_MESSAGE_OFFERED, and leaves on SESSION_END", async () => {
    mockDoorApi();
    render(<App />);
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());

    fireEvent.click(screen.getByText("Ring Bell"));
    await waitFor(() => expect(screen.getByText("Bell sent")).toBeTruthy());
    fireEvent.click(screen.getByText("Open Visitor QR"));
    await waitFor(() => expect(screen.getByText("Visitor Link")).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId("qr-placeholder")).toBeTruthy());

    // The two transitions that used to replace the code mid-sentence.
    act(() => ws.emit(sessionDelta("RINGING", "UNANSWERED_TIMEOUT")));
    expect(screen.getByText("Visitor Link")).toBeTruthy();
    expect(screen.queryByText("No answer yet")).toBeNull();

    act(() => ws.emit(sessionDelta("UNANSWERED_TIMEOUT", "VIDEO_MESSAGE_OFFERED")));
    expect(screen.getByText("Visitor Link")).toBeTruthy();
    expect(screen.queryByText("Leave a Video Message")).toBeNull();
    expect(screen.getByTestId("qr-placeholder")).toBeTruthy();

    // A session that genuinely ends still takes the pad home: the token is dead then.
    act(() => ws.emit(sessionDelta("VIDEO_MESSAGE_OFFERED", "SESSION_END")));
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    expect(screen.queryByText("Visitor Link")).toBeNull();
  });

  it("still follows server transitions on every other screen", async () => {
    // The stickiness is scoped to the link, not a blanket "ignore the server".
    mockDoorApi();
    render(<App />);
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());

    fireEvent.click(screen.getByText("Ring Bell"));
    await waitFor(() => expect(screen.getByText("Bell sent")).toBeTruthy());

    act(() => ws.emit(sessionDelta("RINGING", "UNANSWERED_TIMEOUT")));
    expect(screen.getByText("No answer yet")).toBeTruthy();

    act(() => ws.emit(sessionDelta("UNANSWERED_TIMEOUT", "VIDEO_MESSAGE_OFFERED")));
    expect(screen.getByText("Leave a Video Message")).toBeTruthy();
  });
});
