// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { GuestbookQuote, PollOptionRow } from "./SocialRenderers";
import { AdminSocialPanel } from "./AdminSocialPanel";
import { VisitorPage } from "./VisitorPage";
import { App } from "./App";

const HOSTILE = "<script>alert(1)</script>";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
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
  const fetchMock = vi.fn(async () => {
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
    mockFetchSequence([
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
