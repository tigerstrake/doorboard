// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { fireEvent, render, screen, cleanup, waitFor } from "@testing-library/react";

// These tests exercise build-time env-driven behavior (VITE_FEATURE_PHOTOBOOTH,
// VITE_ROOM_LABEL, VITE_RESIDENTS), which App.tsx reads into module-level
// constants. To flip those we stub the env, reset the module registry, and
// import App dynamically so the constants re-evaluate against the stub.
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

// A URL-aware fetch mock covering every endpoint the post-bell photo check-in
// touches: mount /session, the ring, the auto-capture, save/discard, the
// visitor token, and the check-in.
function stubPhotoBoothFetch() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    let body: unknown = { session: { state: "IDLE" }, config: {} };
    if (url.includes("/doorpad/ring")) {
      body = { accepted: true, session: { state: "RINGING", session_id: "sess-1" } };
    } else if (url.includes("/photo-booth/capture")) {
      body = {
        photo: {
          recording_id: "rec-photo-1",
          session_id: "sess-1",
          review_url: "blob:mock-photo",
          size_bytes: 1234,
          sha256: "abc123",
        },
      };
    } else if (url.includes("/save")) {
      body = { accepted: true };
    } else if (url.includes("/discard")) {
      body = { accepted: true };
    } else if (url.includes("/visitor-token")) {
      body = { token: "tok-1", url: "http://door.local/visitor?token=tok-1" };
    } else if (url.includes("/checkins")) {
      body = { id: "checkin-1", person_id: null, label: "Guest", created_at: "now" };
    }
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// Minimal fetch mock for the config-driven heading/subtitle tests.
function stubBasicFetch() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    let body: unknown = { session: { state: "IDLE" }, config: {} };
    if (url.includes("/guestbook")) body = { entries: [] };
    else if (url.includes("/polls/current")) body = { poll: null };
    else if (url.includes("/visitor-token")) body = { token: "t", url: "u" };
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function checkinBody(fetchMock: ReturnType<typeof vi.fn>): Record<string, unknown> | null {
  const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/checkins"));
  if (!call) return null;
  const init = call[1] as RequestInit | undefined;
  return init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
}

describe("Post-bell photo check-in flow", () => {
  it("auto-captures after the bell and shows the opt-in prompt + collage subtitle", async () => {
    vi.stubEnv("VITE_FEATURE_PHOTOBOOTH", "true");
    window.history.pushState(null, "", "/doorpad");
    const fetchMock = stubPhotoBoothFetch();

    const App = await loadApp();
    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Ring Bell"));

    await waitFor(
      () => expect(screen.getByText("Want to check in with your picture?")).toBeTruthy(),
      { timeout: 4000 }
    );
    expect(
      screen.getByText(/fun end-of-year collage of everyone who stopped by/)
    ).toBeTruthy();
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("/photo-booth/capture"))
    ).toBe(true);
    await waitFor(() => expect(screen.getByAltText("Your check-in photo")).toBeTruthy());
  });

  it("saves the photo and creates a check-in with the photo_recording_id on Yes", async () => {
    vi.stubEnv("VITE_FEATURE_PHOTOBOOTH", "true");
    window.history.pushState(null, "", "/doorpad");
    const fetchMock = stubPhotoBoothFetch();

    const App = await loadApp();
    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Ring Bell"));
    await waitFor(() => expect(screen.getByAltText("Your check-in photo")).toBeTruthy(), {
      timeout: 4000,
    });

    fireEvent.click(screen.getByText("Yes, check in"));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).includes("/photo-booth/rec-photo-1/save")
        )
      ).toBe(true)
    );
    await waitFor(() => {
      const body = checkinBody(fetchMock);
      expect(body).toBeTruthy();
      expect(body?.photo_recording_id).toBe("rec-photo-1");
    });
    await waitFor(() => expect(screen.getByText("Checked in — thanks!")).toBeTruthy());
  });

  it("discards the auto-captured photo and does not check in on No thanks", async () => {
    vi.stubEnv("VITE_FEATURE_PHOTOBOOTH", "true");
    window.history.pushState(null, "", "/doorpad");
    const fetchMock = stubPhotoBoothFetch();

    const App = await loadApp();
    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Ring Bell"));
    await waitFor(() => expect(screen.getByAltText("Your check-in photo")).toBeTruthy(), {
      timeout: 4000,
    });

    fireEvent.click(screen.getByText("No thanks"));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).includes("/photo-booth/rec-photo-1/discard")
        )
      ).toBe(true)
    );
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("/checkins"))
    ).toBe(false);
    await waitFor(() =>
      expect(screen.queryByText("Want to check in with your picture?")).toBeNull()
    );
  });

  it("keeps the plain Check In action when the photo booth flag is off", async () => {
    // No VITE_FEATURE_PHOTOBOOTH stub -> flag off (default).
    window.history.pushState(null, "", "/doorpad");
    stubPhotoBoothFetch();

    const App = await loadApp();
    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Ring Bell"));
    await waitFor(() => expect(screen.getByText("Bell sent")).toBeTruthy());
    expect(document.querySelector("#post-ring-checkin")).toBeTruthy();
    expect(screen.queryByText("Want to check in with your picture?")).toBeNull();
  });
});

describe("Configurable room label and residents", () => {
  it("uses VITE_ROOM_LABEL in the DoorPad heading", async () => {
    vi.stubEnv("VITE_ROOM_LABEL", "512");
    window.history.pushState(null, "", "/doorpad");
    stubBasicFetch();

    const App = await loadApp();
    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 512 DoorPad")).toBeTruthy());
    expect(screen.queryByText("Room 304 DoorPad")).toBeNull();
  });

  it("uses VITE_ROOM_LABEL in the Wallboard heading", async () => {
    vi.stubEnv("VITE_ROOM_LABEL", "512");
    window.history.pushState(null, "", "/wallboard");
    stubBasicFetch();

    const App = await loadApp();
    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 512 Wallboard")).toBeTruthy());
  });

  it("surfaces VITE_RESIDENTS as a friendly subtitle on the DoorPad", async () => {
    vi.stubEnv("VITE_RESIDENTS", "Tiger, Adam");
    window.history.pushState(null, "", "/doorpad");
    stubBasicFetch();

    const App = await loadApp();
    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    expect(screen.getByText("Tiger & Adam")).toBeTruthy();
  });

  it("renders no resident subtitle when VITE_RESIDENTS is unset", async () => {
    window.history.pushState(null, "", "/doorpad");
    stubBasicFetch();

    const App = await loadApp();
    const { container } = render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    expect(container.querySelector(".doorpad-residents")).toBeNull();
  });
});
