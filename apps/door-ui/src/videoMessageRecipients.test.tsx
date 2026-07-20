// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { fireEvent, render, screen, cleanup, waitFor } from "@testing-library/react";

// The DoorPad video-message review step derives its "Send to …" recipient
// buttons from VITE_RESIDENTS, which App.tsx reads into a module-level constant.
// To flip it we stub the env, reset the module registry, and import App
// dynamically so the constant re-evaluates against the stub (same trick as
// photoCheckin.test.tsx).
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
  window.localStorage.clear();
  window.history.pushState(null, "", "/");
});

async function loadApp() {
  const mod = await import("./App");
  return mod.App;
}

// A URL-aware fetch mock covering the whole video-message flow: mount /session,
// the offer/start/stop transitions, the latest-recording poll, plus save and
// discard. Every endpoint returns an "accepted" snapshot so the client-side
// state machine advances offer -> countdown -> recording -> review.
function stubVideoMessageFetch() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    let body: unknown = { session: { state: "IDLE" }, config: {} };
    if (url.includes("/video-message/offer")) {
      body = {
        accepted: true,
        session: { state: "VIDEO_MESSAGE_OFFERED" },
        config: { max_recording_s: 60 },
      };
    } else if (url.includes("/video-message/start")) {
      body = { accepted: true, session: { state: "VIDEO_MESSAGE_RECORDING" } };
    } else if (url.includes("/video-message/stop")) {
      body = { accepted: true, session: { state: "VIDEO_MESSAGE_REVIEW" } };
    } else if (url.includes("/video-message/latest")) {
      body = {
        recording: {
          recording_id: "rec-video-1",
          session_id: "sess-1",
          consent_context: "visitor_initiated",
          playback_url: "blob:mock-video",
        },
      };
    } else if (url.includes("/video-message/save")) {
      body = { accepted: true, session: { state: "VIDEO_MESSAGE_SAVED" } };
    } else if (url.includes("/video-message/discard")) {
      body = { accepted: true, session: { state: "SESSION_END" } };
    }
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// Drive the DoorPad from the home screen to the video-message review step,
// where the recipient buttons live.
async function driveToReview() {
  await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
  fireEvent.click(screen.getByText("Video Message"));
  fireEvent.click(screen.getByText("Start Recording"));
  // Countdown (3s) auto-advances into recording; wait for the Stop control.
  await waitFor(() => expect(screen.getByText("Stop & Review")).toBeTruthy(), {
    timeout: 6000,
  });
  fireEvent.click(screen.getByText("Stop & Review"));
  await waitFor(() => expect(screen.getByText("Review Message")).toBeTruthy());
}

// The parsed JSON body of the (single) /video-message/save POST, or null.
function saveBody(fetchMock: ReturnType<typeof vi.fn>): Record<string, unknown> | null {
  const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/video-message/save"));
  if (!call) return null;
  const init = call[1] as RequestInit | undefined;
  return init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
}

describe("Video-message recipient buttons (review step)", () => {
  it("renders a Send-to button per resident plus Send to both and Discard", async () => {
    vi.stubEnv("VITE_RESIDENTS", "Tiger, Adam");
    window.history.pushState(null, "", "/doorpad");
    stubVideoMessageFetch();

    const App = await loadApp();
    render(<App />);
    await driveToReview();

    expect(screen.getByText("Send to Tiger")).toBeTruthy();
    expect(screen.getByText("Send to Adam")).toBeTruthy();
    expect(screen.getByText("Send to both")).toBeTruthy();
    expect(screen.getByText("Discard")).toBeTruthy();
    // The plain broadcast "Save Message" is replaced by the recipient choices.
    expect(screen.queryByText("Save Message")).toBeNull();
  }, 15000);

  it("saves with recipients [tiger] when Send to Tiger is tapped", async () => {
    vi.stubEnv("VITE_RESIDENTS", "Tiger, Adam");
    window.history.pushState(null, "", "/doorpad");
    const fetchMock = stubVideoMessageFetch();

    const App = await loadApp();
    render(<App />);
    await driveToReview();

    fireEvent.click(screen.getByText("Send to Tiger"));

    await waitFor(() => expect(saveBody(fetchMock)).toEqual({ recipients: ["tiger"] }));
    await waitFor(() => expect(screen.getByText("Sent to Tiger")).toBeTruthy());
  }, 15000);

  it("saves with all recipient keys when Send to both is tapped", async () => {
    vi.stubEnv("VITE_RESIDENTS", "Tiger, Adam");
    window.history.pushState(null, "", "/doorpad");
    const fetchMock = stubVideoMessageFetch();

    const App = await loadApp();
    render(<App />);
    await driveToReview();

    fireEvent.click(screen.getByText("Send to both"));

    await waitFor(() =>
      expect(saveBody(fetchMock)).toEqual({ recipients: ["tiger", "adam"] })
    );
    await waitFor(() => expect(screen.getByText("Sent to Tiger & Adam")).toBeTruthy());
  }, 15000);

  it("takes the discard path with no save when Discard is tapped", async () => {
    vi.stubEnv("VITE_RESIDENTS", "Tiger, Adam");
    window.history.pushState(null, "", "/doorpad");
    const fetchMock = stubVideoMessageFetch();

    const App = await loadApp();
    render(<App />);
    await driveToReview();

    fireEvent.click(screen.getByText("Discard"));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes("/video-message/discard"))
      ).toBe(true)
    );
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("/video-message/save"))
    ).toBe(false);
  }, 15000);

  it("falls back to a single broadcast Save when VITE_RESIDENTS is unset", async () => {
    // No VITE_RESIDENTS stub -> empty residents.
    window.history.pushState(null, "", "/doorpad");
    const fetchMock = stubVideoMessageFetch();

    const App = await loadApp();
    render(<App />);
    await driveToReview();

    expect(screen.queryByText(/^Send to /)).toBeNull();
    const save = screen.getByText("Save Message");
    fireEvent.click(save);

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes("/video-message/save"))
      ).toBe(true)
    );
    // Broadcast save carries no recipients body.
    expect(saveBody(fetchMock)).toBeNull();
    await waitFor(() => expect(screen.getByText("Message Saved")).toBeTruthy());
  }, 15000);
});
