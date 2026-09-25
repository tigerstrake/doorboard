// @vitest-environment jsdom
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

/**
 * The admin entrance on the DoorPad (ADR-0042) sits on a public, corridor-facing
 * touchscreen with no URL bar. Two things follow that the console did not do:
 *
 * - there has to be a way back to the visitor screen from both admin states, or a
 *   2.5 s touch on the title strands the door on "Admin sign in" until a reboot;
 * - the token must not outlive the visit. It used to be written to localStorage, so
 *   every later /admin opened already unlocked — video inbox, enrolled names, arrival
 *   log — for whoever pressed the title next.
 */

const ADMIN_TOKEN_KEY = "doorboard_admin_social_token";

function mockDoorApi() {
  const fetchMock = vi.fn<(...args: unknown[]) => Promise<Response>>(async (...args) => {
    const url = String(args[0] ?? "");
    let body: unknown = {};
    if (url.includes("/doorpad/activity")) {
      return { ok: true, status: 202, json: async () => ({}) } as Response;
    }
    if (url.endsWith("/session")) {
      body = {
        session: {
          state: "IDLE",
          display_name: "Tiger",
          profile_id: "sunrise",
          attributed_to: "Tiger",
          identity_expires_in_s: 90,
        },
        config: {},
      };
    } else if (url.includes("/admin/guestbook") || url.includes("/moderation-log")) {
      body = { entries: [] };
    } else if (url.includes("/admin/polls")) {
      body = { polls: [] };
    } else if (url.includes("/most-frequent")) {
      body = { stat: null };
    } else if (url.includes("/media-inbox") || url.includes("/admin/recordings")) {
      body = { recordings: [] };
    } else if (url.includes("/consent")) {
      body = { text: "Consent Statement", version: "v3" };
    } else if (url.includes("/health")) {
      body = { privacy_enabled: false };
    } else if (url.includes("/relay-status")) {
      body = { configured: false, status: "disabled" };
    } else if (
      url.includes("/people") ||
      url.includes("/invites") ||
      url.includes("/visits")
    ) {
      body = [];
    }
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function openDoorpadRecognised() {
  window.history.pushState(null, "", "/doorpad");
  const fetchMock = mockDoorApi();
  render(<App />);
  await waitFor(() => expect(screen.getByTestId("doorpad-admin-link")).toBeTruthy());
  return fetchMock;
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.history.pushState(null, "", "/");
});

describe("admin entrance from the DoorPad (ADR-0042)", () => {
  it("offers a way back from the sign-in form, and never persists the token", async () => {
    await openDoorpadRecognised();

    fireEvent.click(screen.getByTestId("doorpad-admin-link"));
    expect(window.location.pathname).toBe("/admin");
    expect(screen.getByText("Admin sign in")).toBeTruthy();

    // The signed-out state has a way back, not just "Continue".
    expect(screen.getByText("Back to DoorPad")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Admin token"), { target: { value: "sekrit" } });
    fireEvent.click(screen.getByText("Continue"));
    await waitFor(() => expect(screen.getByText("Admin Control Panel")).toBeTruthy());

    // Unlocked, and nothing about it on disk.
    expect(window.localStorage.getItem(ADMIN_TOKEN_KEY)).toBeNull();
    expect(Object.keys(window.localStorage).some((key) => key.includes("admin"))).toBe(false);
    // The signed-in header has the same way back.
    expect(screen.getByText("Back to DoorPad")).toBeTruthy();

    fireEvent.click(screen.getByText("Back to DoorPad"));
    expect(window.location.pathname).toBe("/doorpad");
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());

    // Coming back asks for the token again rather than reopening the console.
    fireEvent.click(screen.getByTestId("doorpad-admin-link"));
    expect(screen.getByText("Admin sign in")).toBeTruthy();
    expect(screen.queryByText("Admin Control Panel")).toBeNull();
  });

  it("sends the token as a bearer header from state, not from storage", async () => {
    const fetchMock = await openDoorpadRecognised();
    fireEvent.click(screen.getByTestId("doorpad-admin-link"));
    fireEvent.change(screen.getByLabelText("Admin token"), { target: { value: "sekrit" } });
    fireEvent.click(screen.getByText("Continue"));

    await waitFor(() => {
      const inbox = fetchMock.mock.calls.find(([url]) => String(url).includes("/admin/media-inbox"));
      expect(inbox).toBeTruthy();
      const headers = (inbox?.[1] as RequestInit | undefined)?.headers as Record<string, string>;
      expect(headers.Authorization).toBe("Bearer sekrit");
    });
  });

  it("locks itself and returns to the DoorPad after three idle minutes", async () => {
    await openDoorpadRecognised();
    vi.useFakeTimers();

    fireEvent.click(screen.getByTestId("doorpad-admin-link"));
    fireEvent.change(screen.getByLabelText("Admin token"), { target: { value: "sekrit" } });
    fireEvent.click(screen.getByText("Continue"));
    expect(screen.getByText("Admin Control Panel")).toBeTruthy();

    // A touch two minutes in re-arms the window: the owner is still using it.
    act(() => {
      vi.advanceTimersByTime(2 * 60_000);
    });
    act(() => {
      window.dispatchEvent(new Event("pointerdown"));
    });
    act(() => {
      vi.advanceTimersByTime(2 * 60_000);
    });
    expect(window.location.pathname).toBe("/admin");
    expect(screen.getByText("Admin Control Panel")).toBeTruthy();

    // Three quiet minutes: the console locks and the pad goes back to the visitor screen.
    act(() => {
      vi.advanceTimersByTime(60_000 + 100);
    });
    expect(window.location.pathname).toBe("/doorpad");
    expect(screen.queryByText("Admin Control Panel")).toBeNull();

    // And the next entry starts from the token prompt.
    fireEvent.click(screen.getByTestId("doorpad-admin-link"));
    expect(screen.getByText("Admin sign in")).toBeTruthy();
  });
});
