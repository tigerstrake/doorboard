// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { fireEvent, render, screen, cleanup, waitFor } from "@testing-library/react";
import { GuestbookQuote, PollOptionRow } from "./SocialRenderers";
import { AdminSocialPanel } from "./AdminSocialPanel";
import { VisitorPage } from "./VisitorPage";
import { App } from "./App";

const HOSTILE = "<script>alert(1)</script>";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.history.pushState(null, "", "/");
});

describe("Guestbook/poll render paths never execute untrusted markup", () => {
  it("renders a hostile guestbook quote as inert text (Wallboard tile path)", () => {
    render(<GuestbookQuote text={HOSTILE} authorLabel={HOSTILE} />);
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByText(`"${HOSTILE}"`)).toBeTruthy();
    expect(document.body.innerHTML).not.toContain("<script>alert(1)</script>");
  });

  it("renders a hostile poll option as inert text (Wallboard tile path)", () => {
    render(<PollOptionRow text={HOSTILE} votes={3} />);
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByText(HOSTILE)).toBeTruthy();
  });
});

function mockFetchSequence(responses: Array<{ status?: number; body: unknown }>) {
  let call = 0;
  const fetchMock = vi.fn<(...args: unknown[]) => Promise<Response>>(async () => {
    const resp = responses[Math.min(call, responses.length - 1)];
    call += 1;
    return {
      ok: (resp.status ?? 200) < 400,
      status: resp.status ?? 200,
      json: async () => resp.body,
    } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("AdminSocialPanel renders untrusted guestbook text inertly", () => {
  beforeEach(() => {
    window.localStorage.setItem("doorboard_admin_social_token", "test-token");
  });

  it("shows hostile pending-entry text as plain text, never executes it", async () => {
    mockFetchSequence([
      { body: { entries: [{ id: "e1", text: HOSTILE, author_label: null, created_at: "now" }] } },
      { body: { entries: [] } },
      { body: { polls: [] } },
      { body: { stat: null } },
      { body: { entries: [] } },
    ]);

    render(<AdminSocialPanel />);

    await waitFor(() => expect(screen.getByText(HOSTILE)).toBeTruthy());
    expect(document.querySelector("script")).toBeNull();
  });
});

describe("VisitorPage renders untrusted poll option text inertly", () => {
  it("shows hostile poll option text as plain text, never executes it", async () => {
    window.history.pushState(null, "", "/visitor?token=signed-token");
    mockFetchSequence([
      {
        body: {
          session_id: "018f6a61-0000-7000-8000-000000000402",
          expires_at: 1_800_000_000,
          state: "IDLE",
        },
      },
      {
        body: {
          poll: {
            id: "p1",
            question: HOSTILE,
            status: "open",
            created_at: "now",
            closed_at: null,
            options: [{ id: "o1", text: HOSTILE }],
          },
        },
      },
      { body: { results: [{ option_id: "o1", text: HOSTILE, votes: 1 }] } },
    ]);

    render(<VisitorPage sessionState="IDLE" />);

    await waitFor(() => expect(screen.getAllByText(HOSTILE).length).toBeGreaterThan(0));
    expect(document.querySelector("script")).toBeNull();
  });
});

describe("Photo booth feature flag", () => {
  it("hides the DoorPad photo booth button when the flag is unset", async () => {
    window.history.pushState(null, "", "/doorpad");
    mockFetchSequence([{ body: { session: { state: "IDLE" }, config: {} } }]);

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    expect(screen.queryByText("Photo Booth")).toBeNull();
  });
});

describe("T-405 public kiosk regressions", () => {
  it("does not show simulation controls on public routes unless explicitly enabled", async () => {
    window.history.pushState(null, "", "/wallboard");
    mockFetchSequence([
      { body: { session: { state: "IDLE" }, config: {} } },
      { body: { entries: [] } },
      { body: { poll: null } },
    ]);

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 Wallboard")).toBeTruthy());
    expect(screen.queryByText("Simulation Panel")).toBeNull();
    expect(screen.queryByText("Open Sim Control")).toBeNull();
  });

  it("renders the visitor page without the old route/debug wrapper", async () => {
    window.history.pushState(null, "", "/visitor?token=signed-token");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes("/visitor-session")
        ? {
            session_id: "018f6a61-0000-7000-8000-000000000402",
            expires_at: 1_800_000_000,
            state: "IDLE",
          }
        : url.includes("/polls/current")
          ? { poll: null }
          : { session: { state: "IDLE" }, config: {} };
      return { ok: true, status: 200, json: async () => body } as Response;
    }));

    render(<App />);

    await waitFor(() => expect(screen.getByText("Ring status")).toBeTruthy());
    expect(screen.queryByText("Visitor Surface")).toBeNull();
    expect(screen.queryByText("Back to Navigation")).toBeNull();
  });

  it("rejects a visitor page without a signed token", async () => {
    window.history.pushState(null, "", "/visitor");
    mockFetchSequence([{ status: 401, body: { detail: { error: { code: "invalid" } } } }]);
    render(<VisitorPage sessionState="IDLE" />);
    await waitFor(() => expect(screen.getByText("Visitor link unavailable")).toBeTruthy());
    expect(screen.queryByText("Ring status")).toBeNull();
  });

  it("submits DoorPad free-text guestbook notes through the real social API", async () => {
    window.history.pushState(null, "", "/doorpad");
    const fetchMock = mockFetchSequence([
      { body: { session: { state: "IDLE" }, config: {} } },
      {
        body: {
          id: "entry-1",
          text: "I stopped by",
          author_label: null,
          created_at: "2026-07-08T00:00:00Z",
        },
      },
    ]);

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Guestbook"));
    fireEvent.change(screen.getByPlaceholderText("Or write your own note..."), {
      target: { value: "I stopped by" },
    });
    fireEvent.click(screen.getByText("Submit Note"));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/guestbook"))).toBe(true);
    });
  });

  it("uses poll option taps as the only DoorPad vote submission path", async () => {
    window.history.pushState(null, "", "/doorpad");
    mockFetchSequence([
      { body: { session: { state: "IDLE" }, config: {} } },
      {
        body: {
          poll: {
            id: "poll-1",
            question: "Pick one",
            status: "open",
            created_at: "2026-07-08T00:00:00Z",
            closed_at: null,
            options: [{ id: "a", text: "A" }],
          },
        },
      },
      { body: { results: [{ option_id: "a", text: "A", votes: 0 }] } },
    ]);

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Vote in Poll"));
    await waitFor(() => expect(screen.getByText("Pick one")).toBeTruthy());
    expect(screen.queryByText("Submit Vote")).toBeNull();
  });

  it("does not offer a fake privacy deletion success when no local content exists", async () => {
    window.history.pushState(null, "", "/doorpad");
    mockFetchSequence([{ body: { session: { state: "IDLE" }, config: {} } }]);

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Privacy & Info"));
    const deleteButton = screen.getByText("Request Deletion of My Data").closest("button");
    expect(deleteButton?.disabled).toBe(true);
  });

  it("shows Check In immediately after ringing without waiting for the network", async () => {
    window.history.pushState(null, "", "/doorpad");
    mockFetchSequence([{ body: { session: { state: "IDLE" }, config: {} } }, { status: 500, body: {} }]);

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Ring Bell"));
    expect(screen.getByText("You rang")).toBeTruthy();
    expect(document.querySelector("#post-ring-checkin")).toBeTruthy();
  });

  it("lets DoorPad choose a mock Wallboard focused channel locally", async () => {
    window.history.pushState(null, "", "/doorpad");
    mockFetchSequence([{ body: { session: { state: "IDLE" }, config: {} } }]);

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Wallboard Control"));
    fireEvent.click(screen.getByText("Flights"));

    const stored = window.localStorage.getItem("doorboard_wallboard_focus_request");
    expect(stored).toContain('"mode":"focus"');
    expect(stored).toContain('"channel":"aircraft"');
  });
});
