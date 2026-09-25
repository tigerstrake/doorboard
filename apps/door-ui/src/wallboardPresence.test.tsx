// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import type { PresenceLabel } from "@doorboard/contracts";
import { App, presenceLabelAt } from "./App";

// Presence by subject, never by arrival order (ADR-0048 §3). The board defaults to
// VITE_PRESENCE_SUBJECTS="owner,roommate", so row 1 is the owner's and row 2 the roommate's
// whatever order their events arrive in — and a row reads "Unknown" until an event for
// THAT subject has arrived.

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
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

// Mirrors packages/event-client's BroadcastChannel transport, exactly as the e2e helpers
// do: the same client code path a real /ws delta takes, no backend needed.
let counter = 0;
function publishPresence(subjectId: string, label: PresenceLabel, until: string | null = null) {
  counter += 1;
  new BroadcastChannel("doorboard-events").postMessage({
    event_id: `evt-${counter}`,
    type: "status.presence_changed",
    source: "test",
    occurred_at: new Date().toISOString(),
    monotonic_ms: counter,
    door_id: "primary",
    trace_id: `trace-${counter}`,
    payload: { subject_id: subjectId, label, source: "manual", until },
  });
}

function presenceRow(subjectId: string) {
  const tile = screen.getByText("Presence").closest(".db-tile") as HTMLElement;
  return within(tile).getByText((_, node) =>
    node?.classList.contains("presence-row") === true && node.getAttribute("data-subject") === subjectId,
  );
}

describe("presenceLabelAt", () => {
  it("is unknown with no entry, the label while fresh, and unknown once `until` has passed", () => {
    const at = Date.parse("2026-09-25T06:00:00Z");
    expect(presenceLabelAt(undefined, at)).toBe("unknown");
    const entry = { label: "sleeping" as const, occurredAt: "2026-09-24T23:00:00Z", until: "2026-09-25T07:00:00Z" };
    expect(presenceLabelAt(entry, at)).toBe("sleeping");
    expect(presenceLabelAt(entry, Date.parse("2026-09-25T07:00:00Z"))).toBe("unknown");
    expect(presenceLabelAt({ ...entry, until: null }, Date.parse("2027-01-01T00:00:00Z"))).toBe("sleeping");
  });
});

describe("Wallboard presence tile (ADR-0048)", () => {
  beforeEach(() => {
    window.history.pushState(null, "", "/wallboard");
    mockWallboardFetch();
  });

  it("keeps a fixed row per configured subject and never assigns a label by arrival order", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText("Presence")).toBeTruthy());

    // Before any event: both rows exist, both unknown, owner first.
    const rowsBefore = document.querySelectorAll(".presence-tile-content .presence-row");
    expect(Array.from(rowsBefore).map((row) => row.getAttribute("data-subject"))).toEqual([
      "owner",
      "roommate",
    ]);
    expect(presenceRow("owner").textContent).toContain("Unknown");
    expect(presenceRow("roommate").textContent).toContain("Unknown");

    // The roommate's status moves first. It must land on the roommate's row — the old
    // code put the first-arriving subject on Resident 1 (the owner's name).
    publishPresence("roommate", "sleeping");
    await waitFor(() => expect(presenceRow("roommate").textContent).toContain("Recovery"));
    expect(presenceRow("owner").textContent).toContain("Unknown");
    expect(presenceRow("owner").textContent).toContain("Resident 1");

    publishPresence("owner", "social");
    await waitFor(() => expect(presenceRow("owner").textContent).toContain("Come In"));
    expect(presenceRow("roommate").textContent).toContain("Recovery");
  });

  it("drops events for subjects that are not configured instead of inventing a row", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText("Presence")).toBeTruthy());
    publishPresence("guest", "available");
    publishPresence("owner", "busy");
    await waitFor(() => expect(presenceRow("owner").textContent).toContain("Working"));
    expect(document.querySelectorAll(".presence-tile-content .presence-row")).toHaveLength(2);
    expect(document.querySelector('[data-subject="guest"]')).toBeNull();
  });

  it("expires a time-bounded label on the clock tick, even with no new event", async () => {
    // Fake timers that still advance with real time, so BroadcastChannel delivery and
    // waitFor keep working while the 1 s clock tick and Date can be jumped forward.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<App />);
    await waitFor(() => expect(screen.getByText("Presence")).toBeTruthy());

    // ADR-0037's nightly window: "sleeping until 07:00" — here, until 10 s from now.
    publishPresence("owner", "sleeping", new Date(Date.now() + 10_000).toISOString());
    await waitFor(() => expect(presenceRow("owner").textContent).toContain("Recovery"));

    await act(async () => {
      vi.advanceTimersByTime(11_000);
    });
    expect(presenceRow("owner").textContent).toContain("Unknown");
  });
});
