// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { App } from "./App";

// Honest staleness: a tile's badge measures when its feed last answered, not how old the
// content it shows happens to be. A guestbook that is re-fetched every 30 s and holds a
// week-old note is fresh; labelling it "Stale" cries wolf and trains the corridor to ignore
// the one badge that matters (the NUC-outage case).

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  window.localStorage.clear();
  window.history.pushState(null, "", "/");
});

const WEEK_OLD_NOTE = {
  id: "gb-week-old",
  text: "Still the best door on the floor.",
  author_label: "A neighbour",
  status: "approved",
  created_at: new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString(),
};

function mockWallboardFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes("/guestbook")
        ? { entries: [WEEK_OLD_NOTE] }
        : url.includes("/polls/current")
          ? { poll: null }
          : { session: { state: "IDLE" }, config: {} };
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
}

function tileNamed(title: string): HTMLElement {
  return screen.getByText(title).closest(".db-tile") as HTMLElement;
}

describe("Wallboard staleness badges", () => {
  beforeEach(() => {
    window.history.pushState(null, "", "/wallboard");
    mockWallboardFetch();
  });

  it("dates the guestbook tile by the fetch, so a 30 s-old fetch of a week-old note is not stale", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<App />);
    // GuestbookQuote wraps the note in quotation marks, so match on a fragment.
    await waitFor(() => expect(screen.getByText(/best door on the floor/)).toBeTruthy());

    const tile = tileNamed("Guestbook Highlights");
    expect(tile.getAttribute("data-stale")).toBe("false");
    expect(tile.querySelector(".db-tile__staleness")?.textContent).toBe("Just now");

    // Thirty seconds on, past the Tile's own 30 s re-evaluation: still the fetch time.
    await act(async () => {
      vi.advanceTimersByTime(31_000);
    });
    expect(tile.getAttribute("data-stale")).toBe("false");
    expect(tile.querySelector(".db-tile__staleness")?.textContent).toBe("Just now");
  });

  it("keeps the header's live/last-known copy in step with the connection dot", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText("Room 304 Wallboard")).toBeTruthy());
    // Under jsdom the socket never opens, so liveness is "reconnecting": the header must
    // say last-known data, exactly as the dot says reconnecting — never one of each.
    const dot = screen.getByTestId("connection-dot");
    expect(dot.getAttribute("data-liveness")).not.toBe("live");
    expect(document.querySelector(".ambient-header-subtitle")?.textContent).toContain(
      "Using last-known data",
    );
    expect(document.querySelector(".ambient-header-subtitle")?.textContent).not.toContain(
      "Live updates",
    );
  });
});
