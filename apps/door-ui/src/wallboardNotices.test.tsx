// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { App, isClockSynced } from "./App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  window.localStorage.clear();
  window.history.pushState(null, "", "/");
});

function mockWallboardFetch() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
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

describe("isClockSynced (FIX 4 boot-clock guard)", () => {
  // Mid-year dates so the local-time getFullYear() the guard uses can't flip across the
  // year boundary under whatever timezone the test host runs in.
  it("treats a pre-2025 clock (no-RTC boot) as unsynced", () => {
    expect(isClockSynced(new Date("1970-06-15T12:00:00Z"))).toBe(false);
    expect(isClockSynced(new Date("2024-06-15T12:00:00Z"))).toBe(false);
  });

  it("treats 2025 and later as synced", () => {
    expect(isClockSynced(new Date("2025-06-15T12:00:00Z"))).toBe(true);
    expect(isClockSynced(new Date("2026-08-22T12:00:00Z"))).toBe(true);
  });
});

describe("Wallboard public-surface notices", () => {
  it("shows the connection dot and the camera/privacy notice", async () => {
    window.history.pushState(null, "", "/wallboard");
    mockWallboardFetch();

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 Wallboard")).toBeTruthy());

    // FIX 2c: a liveness dot exists so a frozen/reconnecting display is visible.
    expect(screen.getByTestId("connection-dot")).toBeTruthy();

    // FIX 3: the camera/privacy notice is present and states the honest facts.
    const notice = screen.getByLabelText("Camera and privacy notice");
    expect(notice).toBeTruthy();
    expect(notice.textContent).toMatch(/camera/i);
    expect(notice.textContent).toMatch(/opt-in/i);
    expect(notice.textContent).not.toMatch(/nothing is recorded/i);
  });

  it("renders the live clock (not the syncing placeholder) when the clock is synced", async () => {
    // The test host clock is well past 2025, so the wallboard shows a real time.
    window.history.pushState(null, "", "/wallboard");
    mockWallboardFetch();

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 Wallboard")).toBeTruthy());
    expect(screen.queryByText("clock syncing…")).toBeNull();
    expect(document.querySelector(".ambient-clock")?.textContent).toMatch(/\d{1,2}:\d{2}/);
  });

  it("shows the syncing placeholder while the clock still reads ~1970 at boot", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("1970-06-01T00:00:00Z"));
    window.history.pushState(null, "", "/wallboard");
    mockWallboardFetch();

    render(<App />);

    // The wallboard header/clock render synchronously; no fetch needed for the clock.
    expect(screen.getByText("clock syncing…")).toBeTruthy();
    expect(document.querySelector(".ambient-clock")?.textContent).not.toMatch(/\d{1,2}:\d{2}:\d{2}/);
  });
});
