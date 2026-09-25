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

type MockRoute = {
  /** Substring (or pattern) of the request URL this route answers. */
  match: string | RegExp;
  /** HTTP method to answer for; any method when omitted. */
  method?: string;
  status?: number;
  body: unknown;
};

const IDLE_SNAPSHOT = { session: { state: "IDLE" }, config: {} };

/**
 * A URL-keyed fetch mock: each request is answered by the first route found in its URL
 * (and method, when given), never by its position in a sequence. The old sequence mock
 * handed the next canned body to whatever was fetched, so a background request — the
 * identity keepalive, a re-read of `/session`, the visitor token minted on the way into
 * a write — silently re-targeted every later assertion, and a `/visitor-token` 409
 * passed as a success. Unmatched requests get the benign default for the endpoint.
 */
function mockFetchRoutes(routes: MockRoute[] = []) {
  const fetchMock = vi.fn<(...args: unknown[]) => Promise<Response>>(async (...args) => {
    const url = String(args[0] ?? "");
    const method = String((args[1] as RequestInit | undefined)?.method ?? "GET").toUpperCase();
    if (url.includes("/doorpad/activity")) {
      return { ok: true, status: 202, json: async () => ({}) } as Response;
    }
    const route = routes.find(
      (candidate) =>
        (typeof candidate.match === "string"
          ? url.includes(candidate.match)
          : candidate.match.test(url)) &&
        (candidate.method === undefined || candidate.method.toUpperCase() === method)
    );
    let status = 200;
    let body: unknown = {};
    if (route) {
      status = route.status ?? 200;
      body = route.body;
    } else if (url.endsWith("/session")) {
      body = IDLE_SNAPSHOT;
    } else if (url.includes("/visitor-token")) {
      body = { token: "tok-default", url: "http://door.local/visitor?token=tok-default" };
    } else if (url.includes("/guestbook")) {
      body = { entries: [] };
    } else if (url.includes("/polls/current")) {
      body = { poll: null };
    }
    return { ok: status < 400, status, json: async () => body } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function requestBody(
  fetchMock: ReturnType<typeof mockFetchRoutes>,
  match: string,
  method = "POST"
): Record<string, unknown> | null {
  const call = fetchMock.mock.calls.find(
    ([url, init]) =>
      String(url).includes(match) &&
      String((init as RequestInit | undefined)?.method ?? "GET").toUpperCase() === method
  );
  const init = call?.[1] as RequestInit | undefined;
  return init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
}

const PICK_ONE_POLL = {
  poll: {
    id: "poll-1",
    question: "Pick one",
    status: "open",
    created_at: "2026-07-08T00:00:00Z",
    closed_at: null,
    options: [{ id: "a", text: "A" }],
  },
};

describe("AdminSocialPanel renders untrusted guestbook text inertly", () => {
  beforeEach(() => {
    window.localStorage.setItem("doorboard_admin_social_token", "test-token");
  });

  it("shows hostile pending-entry text as plain text, never executes it", async () => {
    mockFetchRoutes([
      {
        match: "status=pending",
        body: { entries: [{ id: "e1", text: HOSTILE, author_label: null, created_at: "now" }] },
      },
      { match: "status=approved", body: { entries: [] } },
      { match: "/admin/polls", body: { polls: [] } },
      { match: "/most-frequent", body: { stat: null } },
      { match: "/moderation-log", body: { entries: [] } },
    ]);

    render(<AdminSocialPanel />);

    await waitFor(() => expect(screen.getByText(HOSTILE)).toBeTruthy());
    expect(document.querySelector("script")).toBeNull();
  });
});

describe("VisitorPage renders untrusted poll option text inertly", () => {
  it("shows hostile poll option text as plain text, never executes it", async () => {
    window.history.pushState(null, "", "/visitor?token=signed-token");
    mockFetchRoutes([
      {
        match: "/visitor-session",
        body: {
          session_id: "018f6a61-0000-7000-8000-000000000402",
          expires_at: 1_800_000_000,
          state: "IDLE",
        },
      },
      {
        match: "/polls/current",
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
      { match: "/results", body: { results: [{ option_id: "o1", text: HOSTILE, votes: 1 }] } },
    ]);

    render(<VisitorPage sessionState="IDLE" />);

    await waitFor(() => expect(screen.getAllByText(HOSTILE).length).toBeGreaterThan(0));
    expect(document.querySelector("script")).toBeNull();
  });
});

describe("Photo booth feature flag", () => {
  it("hides the DoorPad photo booth button when the flag is unset", async () => {
    window.history.pushState(null, "", "/doorpad");
    mockFetchRoutes();

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    expect(screen.queryByText("Photo Booth")).toBeNull();
  });
});

describe("T-405 public kiosk regressions", () => {
  it("does not show simulation controls on public routes unless explicitly enabled", async () => {
    window.history.pushState(null, "", "/wallboard");
    mockFetchRoutes();

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 Wallboard")).toBeTruthy());
    expect(screen.queryByText("Simulation Panel")).toBeNull();
    expect(screen.queryByText("Open Sim Control")).toBeNull();
  });

  it("renders the visitor page without the old route/debug wrapper", async () => {
    window.history.pushState(null, "", "/visitor?token=signed-token");
    mockFetchRoutes([
      {
        match: "/visitor-session",
        body: {
          session_id: "018f6a61-0000-7000-8000-000000000402",
          expires_at: 1_800_000_000,
          state: "IDLE",
        },
      },
    ]);

    render(<App />);

    await waitFor(() => expect(screen.getByText("Ring status")).toBeTruthy());
    expect(screen.queryByText("Visitor Surface")).toBeNull();
    expect(screen.queryByText("Back to Navigation")).toBeNull();
  });

  it("rejects a visitor page without a signed token", async () => {
    window.history.pushState(null, "", "/visitor");
    mockFetchRoutes([
      { match: "/visitor-token", status: 401, body: { detail: { error: { code: "invalid" } } } },
      { match: "/visitor-session", status: 401, body: { detail: { error: { code: "invalid" } } } },
    ]);
    render(<VisitorPage sessionState="IDLE" />);
    await waitFor(() => expect(screen.getByText("Visitor link unavailable")).toBeTruthy());
    expect(screen.queryByText("Ring status")).toBeNull();
  });

  it("submits DoorPad free-text guestbook notes through the real social API", async () => {
    window.history.pushState(null, "", "/doorpad");
    const fetchMock = mockFetchRoutes([
      {
        match: "/guestbook",
        method: "POST",
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
    fireEvent.change(screen.getByPlaceholderText("Tap here to type your own note..."), {
      target: { value: "I stopped by" },
    });
    fireEvent.click(screen.getByText("Submit Note"));

    await waitFor(() => {
      expect(requestBody(fetchMock, "/guestbook")).toMatchObject({ text: "I stopped by" });
    });
  });

  it("requires a poll selection before the DoorPad submits a vote", async () => {
    window.history.pushState(null, "", "/doorpad");
    mockFetchRoutes([
      { match: "/polls/current", body: PICK_ONE_POLL },
      { match: "/results", body: { results: [{ option_id: "a", text: "A", votes: 0 }] } },
    ]);

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Vote in Poll"));
    await waitFor(() => expect(screen.getByText("Pick one")).toBeTruthy());
    const submit = screen.getByText("Submit Vote").closest("button");
    expect(submit?.disabled).toBe(true);
    // The button's accessible name now includes its tally, so match on the label text.
    fireEvent.click(screen.getByText("A").closest("button")!);
    expect(submit?.disabled).toBe(false);
  });

  it("draws each DoorPad vote button as its own bar", async () => {
    // The doorpad used to be the one surface with no graph — it printed "— N votes" only
    // after you had voted, while the wallboard a metre away showed the live tally.
    window.history.pushState(null, "", "/doorpad");
    mockFetchRoutes([
      {
        match: "/polls/current",
        body: {
          poll: {
            id: "poll-1",
            question: "Pick one",
            status: "open",
            created_at: "2026-07-08T00:00:00Z",
            closed_at: null,
            options: [
              { id: "a", text: "Alpha" },
              { id: "b", text: "Beta" },
            ],
          },
        },
      },
      {
        match: "/results",
        body: {
          results: [
            { option_id: "a", text: "Alpha", votes: 3 },
            { option_id: "b", text: "Beta", votes: 1 },
          ],
        },
      },
    ]);

    render(<App />);
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Vote in Poll"));
    await waitFor(() => expect(screen.getByText("Alpha")).toBeTruthy());

    // Each vote button carries its own bar, so the graph costs no vertical space on a
    // 600px screen. Shares are of the total cast: 3 of 4 is 75%, not 100%.
    const alpha = screen.getByRole("progressbar", { name: "Alpha" });
    expect(alpha.getAttribute("aria-valuenow")).toBe("75");
    expect(screen.getByRole("progressbar", { name: "Beta" }).getAttribute("aria-valuenow")).toBe(
      "25"
    );
    // The leader is marked, and the fill is a share of the width rather than of the leader.
    const fill = alpha.closest("button")!.querySelector(".poll-choice__fill") as HTMLElement;
    expect(fill.style.width).toBe("75%");
  });

  it("does not offer a fake privacy deletion success when no local content exists", async () => {
    window.history.pushState(null, "", "/doorpad");
    mockFetchRoutes();

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(document.getElementById("btn-privacy") as HTMLElement);
    const deleteButton = screen.getByText("Request Deletion of My Data").closest("button");
    expect(deleteButton?.disabled).toBe(true);
  });

  it("never shows the ringing screen when the ring POST fails, and permits retry", async () => {
    // Updated for the honest-ring fix (was "shows post-ring choices immediately"): the UI
    // used to flip to RINGING optimistically, so a ring the door-api never confirmed still
    // showed the ringing screen. Now a failed POST keeps the visitor on home and surfaces a
    // retry on the ring button instead of pretending the bell fired.
    window.history.pushState(null, "", "/doorpad");
    const fetchMock = mockFetchRoutes([{ match: "/doorpad/ring", status: 500, body: {} }]);

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Ring Bell"));

    // The POST fails, so the ring button surfaces a retry — and the ringing screen and its
    // post-ring choices must NOT appear, because the bell was never confirmed.
    await waitFor(() => expect(screen.getByText(/Bell Unavailable/)).toBeTruthy());
    expect(screen.queryByText("Bell sent")).toBeNull();
    expect(screen.queryByText("Live view at the door")).toBeNull();
    expect(document.querySelector("#post-ring-checkin")).toBeNull();

    fireEvent.click(screen.getByText(/Bell Unavailable/));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(([url]) => String(url).includes("/doorpad/ring"))
      ).toHaveLength(2);
    });
  });

  it("shows a calm chime-offline aside when a ring is accepted but the ESP32 effect failed", async () => {
    // accepted:true means the resident IS notified through the session path, so we still
    // show the ringing screen — but effect.status "unavailable" means the local light/chime
    // did not fire, so we add a calm aside rather than overstate the physical bell.
    window.history.pushState(null, "", "/doorpad");
    mockFetchRoutes([
      {
        match: "/doorpad/ring",
        body: {
          accepted: true,
          effect: { status: "unavailable" },
          session: { state: "RINGING", session_id: "sess-effect" },
        },
      },
    ]);

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Ring Bell"));

    await waitFor(() => expect(screen.getByText("Bell sent")).toBeTruthy());
    expect(screen.getByText(/Bell chime offline/)).toBeTruthy();
    expect(screen.getByText(/they've still been notified/)).toBeTruthy();
  });

  it("ignores malformed and oversized kiosk-local content history", async () => {
    window.history.pushState(null, "", "/doorpad");
    const oversizedStore: Record<string, unknown> = Object.fromEntries(
      Array.from({ length: 24 }, (_, index) => [
        `old-session-${index}`,
        [{ kind: "guestbook", id: `entry-${index}`, label: `Note ${index}` }],
      ])
    );
    oversizedStore["bad-session"] = [{ kind: "guestbook", id: "", label: {} }];
    window.localStorage.setItem("doorboard_my_social_content_v2", JSON.stringify(oversizedStore));
    mockFetchRoutes([
      {
        match: "/guestbook",
        method: "POST",
        body: {
          id: "entry-current",
          text: "Current note",
          author_label: null,
          created_at: "2026-07-14T00:00:00Z",
        },
      },
    ]);

    render(<App />);
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Guestbook"));
    fireEvent.change(screen.getByPlaceholderText("Tap here to type your own note..."), {
      target: { value: "Current note" },
    });
    fireEvent.click(screen.getByText("Submit Note"));

    await waitFor(() => {
      const stored = JSON.parse(
        window.localStorage.getItem("doorboard_my_social_content_v2") ?? "{}"
      ) as Record<string, unknown>;
      expect(Object.keys(stored).length).toBeLessThanOrEqual(16);
      expect(stored["bad-session"]).toBeUndefined();
    });
  });

  it("rolls a failed video-message offer back to the reviewable offer screen", async () => {
    window.history.pushState(null, "", "/doorpad");
    mockFetchRoutes([{ match: "/doorpad/video-message/offer", status: 503, body: {} }]);

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Video Message"));
    fireEvent.click(screen.getByText("Start Recording"));
    await waitFor(() => expect(screen.getByText("Start Recording")).toBeTruthy());
    expect(screen.queryByText("Recording Starts In")).toBeNull();
  });

  it("moved the About content off the doorpad, keeping only the data-deletion screen", async () => {
    // The About text is now a wallboard channel (its focused render is covered in
    // wallboardFocusSplit.test.tsx). The doorpad's privacy screen keeps only the
    // visitor-facing "what have I left here, and how do I erase it" controls.
    window.history.pushState(null, "", "/doorpad");
    mockFetchRoutes();

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    expect(screen.queryByTestId("about-doorboard")).toBeNull();

    const privacyTile = document.getElementById("btn-privacy");
    expect(privacyTile).toBeTruthy();
    fireEvent.click(privacyTile as HTMLElement);

    // The About section is gone from the doorpad entirely...
    expect(screen.queryByTestId("about-doorboard")).toBeNull();
    expect(screen.queryByText("If you are not enrolled")).toBeNull();
    // ...but the deletion controls are still here and working.
    expect(screen.getByText("Request Deletion of My Data")).toBeTruthy();
    expect(screen.getByText(/Nothing submitted yet this session/)).toBeTruthy();
  });

  it("lets DoorPad choose a mock Wallboard focused channel locally", async () => {
    window.history.pushState(null, "", "/doorpad");
    mockFetchRoutes();

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Wallboard Control"));
    fireEvent.click(screen.getByText("Flights"));

    const stored = window.localStorage.getItem("doorboard_wallboard_focus_request");
    expect(stored).toContain('"mode":"focus"');
    expect(stored).toContain('"channel":"aircraft"');
  });

  it("offers About as a first-class selectable Wallboard channel from the control panel", async () => {
    // The About tile used to be ambient-only (channel: null); it is now individually
    // focusable from the launcher, exactly like Flights, satellite, birds, etc.
    window.history.pushState(null, "", "/doorpad");
    mockFetchRoutes();

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Wallboard Control"));
    fireEvent.click(screen.getByText("About this doorboard"));

    const stored = window.localStorage.getItem("doorboard_wallboard_focus_request");
    expect(stored).toContain('"mode":"focus"');
    expect(stored).toContain('"channel":"about"');
  });
});

/**
 * A stranger who has not rung. Every social write first mints a visitor token, and
 * door-api used to answer 409 from IDLE — which the doorpad showed as "request failed
 * with status 409". door-api now opens a doorpad session from IDLE for that call; the
 * doorpad's side is to say door-api's own sentence when it does refuse, and to carry the
 * minted token into the write when it does not.
 */
describe("Social writes from an unrecognised visitor who has not rung", () => {
  beforeEach(() => {
    window.history.pushState(null, "", "/doorpad");
  });

  it("shows door-api's refusal sentence, not a status code", async () => {
    mockFetchRoutes([
      { match: "/visitor-token", status: 409, body: { detail: "No active visitor session" } },
    ]);

    render(<App />);
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(screen.getByText("Guestbook"));
    fireEvent.change(screen.getByPlaceholderText("Tap here to type your own note..."), {
      target: { value: "Hello from the corridor" },
    });
    fireEvent.click(screen.getByText("Submit Note"));

    await waitFor(() =>
      expect(document.querySelector(".db-toast-message")?.textContent).toContain(
        "No active visitor session"
      )
    );
    expect(document.querySelector(".db-toast-message")?.textContent).not.toMatch(/status 409/);
  });

  it("completes the guestbook, vote and check-in once /visitor-token opens a session from IDLE", async () => {
    const fetchMock = mockFetchRoutes([
      {
        match: "/visitor-token",
        body: { token: "tok-idle", url: "http://door.local/visitor?token=tok-idle" },
      },
      {
        match: "/guestbook",
        method: "POST",
        body: { id: "entry-9", text: "Hello", author_label: null, created_at: "now" },
      },
      { match: "/polls/current", body: PICK_ONE_POLL },
      { match: "/results", body: { results: [{ option_id: "a", text: "A", votes: 0 }] } },
      { match: "/polls/poll-1/vote", method: "POST", body: {} },
      {
        match: "/checkins",
        method: "POST",
        body: { id: "checkin-9", label: "Guest", created_at: "now", is_recognized: false },
      },
    ]);

    render(<App />);
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());

    // Guestbook: the write carries the freshly minted token.
    fireEvent.click(screen.getByText("Guestbook"));
    fireEvent.change(screen.getByPlaceholderText("Tap here to type your own note..."), {
      target: { value: "Hello" },
    });
    fireEvent.click(screen.getByText("Submit Note"));
    await waitFor(() =>
      expect(requestBody(fetchMock, "/guestbook")).toMatchObject({
        text: "Hello",
        session_token: "tok-idle",
      })
    );
    await waitFor(() => expect(screen.getByText(/Note submitted!/)).toBeTruthy());

    // Poll: back on home after the note, the vote goes through on the same session.
    fireEvent.click(screen.getByText("Vote in Poll"));
    await waitFor(() => expect(screen.getByText("Pick one")).toBeTruthy());
    fireEvent.click(screen.getByText("A").closest("button")!);
    fireEvent.click(screen.getByText("Submit Vote"));
    await waitFor(() => expect(screen.getByText("Vote submitted.")).toBeTruthy());
    expect(requestBody(fetchMock, "/polls/poll-1/vote")).toMatchObject({
      option_id: "a",
      session_token: "tok-idle",
    });
    fireEvent.click(screen.getByText("Done"));

    // Check-in as Guest: one tap, same token.
    fireEvent.click(screen.getByText("Visitor Check-In"));
    fireEvent.click(screen.getByText("Check in as Guest"));
    await waitFor(() =>
      expect(requestBody(fetchMock, "/checkins")).toMatchObject({
        label: "Guest",
        session_token: "tok-idle",
      })
    );
    await waitFor(() => expect(screen.getByText("Checked in as Guest")).toBeTruthy());
  });
});
