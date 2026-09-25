// @vitest-environment jsdom
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { FakeWebSocket } from "./test/fakeWebSocket";

/**
 * What the pad remembers between visitors, and for how long.
 *
 * - A guestbook draft was cleared only on a successful submit, so a note typed and
 *   abandoned greeted the next person, keyboard still open.
 * - "My Data & Privacy" was keyed on the server session and cleared on IDLE, which
 *   door-api reaches three seconds after every write — so the list forgot a note the
 *   moment it was posted, and the deletion path it exists for was empty.
 */

const NOTE = "Half a thought";

function mockDoorApi() {
  const fetchMock = vi.fn<(...args: unknown[]) => Promise<Response>>(async (...args) => {
    const url = String(args[0] ?? "");
    const method = String((args[1] as RequestInit | undefined)?.method ?? "GET").toUpperCase();
    let body: unknown = {};
    if (url.includes("/doorpad/activity")) {
      return { ok: true, status: 202, json: async () => ({}) } as Response;
    }
    if (url.endsWith("/session")) {
      body = { session: { state: "IDLE", session_id: null }, config: {} };
    } else if (url.includes("/visitor-token")) {
      body = { token: "tok-visit", url: "http://door.local/visitor?token=tok-visit" };
    } else if (url.includes("/guestbook") && method === "POST") {
      body = { id: "entry-draft", text: NOTE, author_label: null, created_at: "now" };
    } else if (url.includes("/guestbook")) {
      body = { entries: [] };
    } else if (url.includes("/polls/current")) {
      body = { poll: null };
    }
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function openDoorpad() {
  window.history.pushState(null, "", "/doorpad");
  mockDoorApi();
  render(<App />);
  await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
}

async function submitNote() {
  fireEvent.click(screen.getByText("Guestbook"));
  fireEvent.change(screen.getByPlaceholderText("Tap here to type your own note..."), {
    target: { value: NOTE },
  });
  fireEvent.click(screen.getByText("Submit Note"));
  await waitFor(() => expect(screen.getByText(/Note submitted!/)).toBeTruthy());
  await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  // jsdom has no layout, so the composer's scroll-into-view on focus needs a stand-in.
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.pushState(null, "", "/");
});

describe("guestbook drafts", () => {
  it("does not show an unsent draft to the next visitor", async () => {
    await openDoorpad();

    fireEvent.click(screen.getByText("Guestbook"));
    const textarea = screen.getByPlaceholderText(
      "Tap here to type your own note..."
    ) as HTMLTextAreaElement;
    fireEvent.focus(textarea);
    fireEvent.change(textarea, { target: { value: NOTE } });
    expect(textarea.value).toBe(NOTE);
    expect(screen.getByLabelText("On-screen keyboard")).toBeTruthy();

    fireEvent.click(screen.getByText("Cancel"));
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());

    fireEvent.click(screen.getByText("Guestbook"));
    const reopened = screen.getByPlaceholderText(
      "Tap here to type your own note..."
    ) as HTMLTextAreaElement;
    expect(reopened.value).toBe("");
    expect(screen.getByText("0 / 280")).toBeTruthy();
    expect(screen.queryByLabelText("On-screen keyboard")).toBeNull();
  });

  it("forgets a picked phrase along with the text", async () => {
    await openDoorpad();

    fireEvent.click(screen.getByText("Guestbook"));
    fireEvent.click(screen.getByText('"Call me later!"'));
    expect(screen.getByText("Send Selected Phrase")).toBeTruthy();

    fireEvent.click(screen.getByText("Cancel"));
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Guestbook"));
    expect(screen.queryByText("Send Selected Phrase")).toBeNull();
  });
});

describe("My Data & Privacy", () => {
  it("still lists a submission after the server session lapses to IDLE", async () => {
    await openDoorpad();
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());

    await submitNote();

    // door-api's linger after a write: back to IDLE with nobody held.
    act(() =>
      ws.emit({
        type: "snapshot",
        state: {
          session_id: null,
          state: "IDLE",
          display_name: null,
          profile_id: null,
          attributed_to: null,
          identity_expires_in_s: 0,
        },
      })
    );

    fireEvent.click(document.getElementById("btn-privacy") as HTMLElement);
    expect(screen.getByText(/Things you've submitted this session/)).toBeTruthy();
    expect(screen.getByText(`guestbook: ${NOTE}`)).toBeTruthy();
    const deleteAll = screen.getByText("Request Deletion of My Data").closest("button");
    expect(deleteAll?.disabled).toBe(false);
  });

  it("clears the list after three quiet minutes on the pad", async () => {
    await openDoorpad();
    await submitNote();

    vi.useFakeTimers();
    // A touch re-arms the idle window on the fake clock.
    act(() => {
      window.dispatchEvent(new Event("pointerdown"));
    });
    act(() => {
      vi.advanceTimersByTime(2 * 60_000);
    });
    act(() => {
      window.dispatchEvent(new Event("pointerdown"));
    });
    act(() => {
      vi.advanceTimersByTime(2 * 60_000);
    });
    // Two minutes since the last touch: still remembered.
    fireEvent.click(document.getElementById("btn-privacy") as HTMLElement);
    expect(screen.getByText(`guestbook: ${NOTE}`)).toBeTruthy();
    fireEvent.click(screen.getByText("Back"));

    act(() => {
      vi.advanceTimersByTime(3 * 60_000 + 100);
    });
    vi.useRealTimers();

    fireEvent.click(document.getElementById("btn-privacy") as HTMLElement);
    expect(screen.queryByText(`guestbook: ${NOTE}`)).toBeNull();
    expect(screen.getByText(/Nothing submitted yet this session/)).toBeTruthy();
  });
});
