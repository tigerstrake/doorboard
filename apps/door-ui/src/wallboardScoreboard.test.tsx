// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { App, scoreboardRowName } from "./App";

// The scoreboard event carries only a delta and door-api has no read route, so the tile
// is the sum of deltas received since page load. What it must get right: sum per entry,
// keep boards apart by board_id, name rows by entry (a resident's name for a configured
// subject, a rank label otherwise) rather than by rank position, and say what the total
// is a total *since* — and never invent a carried-over total after a reload.

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.history.pushState(null, "", "/");
});

function mockWallboardFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes("/guestbook")
        ? { entries: [] }
        : url.includes("/polls/current")
          ? { poll: null }
          : { session: { state: "IDLE" }, config: {} };
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
}

let counter = 0;
function publishDelta(boardId: string, entryId: string, delta: number) {
  counter += 1;
  new BroadcastChannel("doorboard-events").postMessage({
    event_id: `evt-${counter}`,
    type: "social.scoreboard_updated",
    source: "test",
    occurred_at: new Date().toISOString(),
    monotonic_ms: counter,
    door_id: "primary",
    trace_id: `trace-${counter}`,
    payload: { board_id: boardId, entry_id: entryId, delta },
  });
}

function scoreboardTile(): HTMLElement {
  return screen.getByText("Scoreboard").closest(".db-tile") as HTMLElement;
}

function rows(): Array<{ entry: string | null; text: string }> {
  return Array.from(scoreboardTile().querySelectorAll(".score-row")).map((row) => ({
    entry: row.getAttribute("data-entry"),
    text: row.textContent ?? "",
  }));
}

describe("scoreboardRowName", () => {
  it("names a configured subject as its resident and everything else by rank", () => {
    expect(scoreboardRowName("owner", 3)).toBe("Resident 1");
    expect(scoreboardRowName("roommate", 0)).toBe("Resident 2");
    expect(scoreboardRowName("prs_9f3", 0)).toBe("Leader");
    expect(scoreboardRowName("prs_9f3", 1)).toBe("Runner-up");
    expect(scoreboardRowName("prs_9f3", 2)).toBe("#3");
  });
});

describe("Wallboard scoreboard tile", () => {
  beforeEach(() => {
    window.history.pushState(null, "", "/wallboard");
    mockWallboardFetch();
  });

  it("sums deltas per entry, names rows by entry, and keeps boards apart", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText("Scoreboard")).toBeTruthy());
    expect(within(scoreboardTile()).getByText("Scoreboard unavailable.")).toBeTruthy();

    publishDelta("daily", "owner", 3);
    publishDelta("daily", "roommate", 4);
    publishDelta("daily", "owner", 2);
    await waitFor(() => expect(rows()).toHaveLength(2));
    await waitFor(() => expect(rows()[0]?.text).toContain("5"));
    // owner: 3 + 2 = 5 leads; the roommate's 4 is second — and each row is labelled by
    // WHO it is, not by its position.
    expect(rows()).toEqual([
      { entry: "owner", text: expect.stringContaining("Resident 1") },
      { entry: "roommate", text: expect.stringContaining("Resident 2") },
    ]);
    expect(rows()[0]?.text).toContain("5 pts");
    expect(rows()[1]?.text).toContain("4 pts");
    expect(screen.getByTestId("scoreboard-board").textContent).toMatch(/^daily · since \d/);

    // A second board does not merge into the first: it is shown on its own, its unknown
    // entry named by rank, and the daily totals survive untouched underneath.
    publishDelta("weekly", "prs_9f3", 9);
    await waitFor(() => expect(screen.getByTestId("scoreboard-board").textContent).toMatch(/^weekly/));
    expect(rows()).toEqual([{ entry: "prs_9f3", text: expect.stringContaining("Leader") }]);

    publishDelta("daily", "roommate", 2);
    await waitFor(() => expect(screen.getByTestId("scoreboard-board").textContent).toMatch(/^daily/));
    // roommate: 4 + 2 = 6 now leads; owner still 5 and still "Resident 1".
    expect(rows()).toEqual([
      { entry: "roommate", text: expect.stringContaining("Resident 2") },
      { entry: "owner", text: expect.stringContaining("Resident 1") },
    ]);
    expect(rows()[0]?.text).toContain("6 pts");
  });

  it("starts from nothing after a reload rather than inventing a carried-over total", async () => {
    const first = render(<App />);
    await waitFor(() => expect(screen.getByText("Scoreboard")).toBeTruthy());
    publishDelta("daily", "owner", 3);
    publishDelta("daily", "owner", 2);
    await waitFor(() => expect(rows()[0]?.text).toContain("5 pts"));

    // Simulated reload: a fresh App has received no deltas. With no read route there is
    // no truthful total to show, so it shows none — not a stale 5, not a fabricated 0.
    first.unmount();
    render(<App />);
    await waitFor(() => expect(screen.getByText("Scoreboard")).toBeTruthy());
    expect(within(scoreboardTile()).getByText("Scoreboard unavailable.")).toBeTruthy();
    expect(rows()).toHaveLength(0);

    publishDelta("daily", "owner", 1);
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(rows()[0]?.text).toContain("1 pts");
  });
});
