// @vitest-environment jsdom
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

/**
 * The review screen after a video message.
 *
 * Two paths reach it without the visitor pressing "Stop & Review": the server's own
 * max-duration stop (door-api's `timeout:max_recording` fires before the client's
 * elapsed timer) and a reload mid-review, which re-derives the screen from GET /session.
 * The preview used to be fetched only from the Stop handler, so both sat on "Preparing
 * the preview" forever. And the Stop handler kept `mediaActionPending` set across the
 * whole preview poll, greying out Send/Discard/Re-record for up to 30 s under copy that
 * promised "you can send it now without waiting".
 */

const LATEST = {
  recording_id: "018f6a61-0000-7000-8000-000000000999",
  playback_url: "data:video/mp4;base64,",
  duration_s: 4.2,
  finalized_at_utc: "2026-09-25T10:00:04Z",
};

function mockDoorApi(options: {
  initialState: string;
  latest: { recording: typeof LATEST | null };
}) {
  const fetchMock = vi.fn<(...args: unknown[]) => Promise<Response>>(async (...args) => {
    const url = String(args[0] ?? "");
    let body: unknown = {};
    if (url.includes("/doorpad/activity")) {
      return { ok: true, status: 202, json: async () => ({}) } as Response;
    }
    if (url.endsWith("/session")) {
      body = {
        session: { state: options.initialState, session_id: "ses-video" },
        config: { max_recording_s: 60 },
      };
    } else if (url.includes("/doorpad/video-message/stop")) {
      body = { accepted: true, session: { state: "VIDEO_MESSAGE_REVIEW", session_id: "ses-video" } };
    } else if (url.includes("/doorpad/video-message/latest")) {
      body = options.latest;
    }
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function latestCalls(fetchMock: ReturnType<typeof mockDoorApi>): number {
  return fetchMock.mock.calls.filter(([url]) => String(url).includes("/video-message/latest"))
    .length;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.pushState(null, "", "/");
});

describe("video message review screen", () => {
  it("fetches the preview when the door arrives in review without the Stop button", async () => {
    // A reload mid-review, or the server's max-duration stop: the screen is derived from
    // the session state, and the preview must follow from that alone.
    window.history.pushState(null, "", "/doorpad");
    const fetchMock = mockDoorApi({
      initialState: "VIDEO_MESSAGE_REVIEW",
      latest: { recording: LATEST },
    });

    render(<App />);

    await waitFor(() => expect(screen.getByText("Review Message")).toBeTruthy());
    await waitFor(() => expect(latestCalls(fetchMock)).toBeGreaterThan(0));
    await waitFor(() => {
      const video = document.querySelector("video.review-video") as HTMLVideoElement | null;
      expect(video?.getAttribute("src")).toBe(LATEST.playback_url);
    });
    // The route's new shape carries no consent field; the screen states the only value it
    // can have here rather than reading one off the payload.
    expect(screen.getByText("Consent context: visitor_initiated")).toBeTruthy();
  });

  it("keeps Send, Discard and Re-record usable while the preview is still preparing", async () => {
    window.history.pushState(null, "", "/doorpad");
    const fetchMock = mockDoorApi({
      initialState: "VIDEO_MESSAGE_RECORDING",
      latest: { recording: null },
    });

    render(<App />);
    await waitFor(() => expect(screen.getByText("Stop & Review")).toBeTruthy());

    fireEvent.click(screen.getByText("Stop & Review"));

    await waitFor(() => expect(screen.getByText("Review Message")).toBeTruthy());
    // The preview never becomes ready, and the buttons do not wait for it.
    expect(screen.getByText(/Preparing the preview/)).toBeTruthy();
    for (const label of ["Save Message", "Re-record", "Discard"]) {
      const button = screen.getByText(label).closest("button");
      expect(button, label).toBeTruthy();
      expect(button?.disabled, `${label} should be enabled`).toBe(false);
    }
    // The preview is still being polled in the background.
    await waitFor(() => expect(latestCalls(fetchMock)).toBeGreaterThan(0));
  });
});
