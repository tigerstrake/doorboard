// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

/**
 * The doorpad's standing "who the door thinks you are" badge, and the tile hints.
 *
 * The complaint these pin: the door greeted someone and then, from the panel's point of
 * view, silently forgot them — the check-in screen offered "Guest" only and nothing on
 * screen explained why. The badge makes the identity window visible while it lasts, and
 * it is driven by the same value the named check-in button reads, so the two can never
 * disagree about whether the door knows you.
 */

function mockSnapshot(session: Record<string, unknown>) {
  const fetchMock = vi.fn<(...args: unknown[]) => Promise<Response>>(async (...args) => {
    // The keepalive is fire-and-forget and must not stand in for a real response.
    if (String(args[0] ?? "").includes("/doorpad/activity")) {
      return { ok: true, status: 202, json: async () => ({}) } as Response;
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({ session, config: {} }),
    } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  window.history.pushState(null, "", "/doorpad");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("doorpad identity badge", () => {
  it("shows the recognised name while the door still knows you", async () => {
    mockSnapshot({
      state: "APPROACH_DETECTED",
      display_name: "Tiger",
      profile_id: "sunrise",
      attributed_to: "Tiger",
      identity_expires_in_s: 104,
    });

    render(<App />);

    const badge = await waitFor(() => screen.getByTestId("doorpad-identity"));
    expect(badge.textContent).toContain("Tiger");
  });

  it("shows nothing when nobody is recognised", async () => {
    mockSnapshot({ state: "IDLE", display_name: null, identity_expires_in_s: 0 });

    render(<App />);

    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    expect(screen.queryByTestId("doorpad-identity")).toBeNull();
  });

  it("keeps the name after the session returns to IDLE", async () => {
    // IDLE no longer means "nobody is here" (ADR-0020): the approach timer expires long
    // before the interaction does, and clearing on it was half of the original bug.
    mockSnapshot({
      state: "IDLE",
      display_name: "Tiger",
      profile_id: "sunrise",
      attributed_to: "Tiger",
      identity_expires_in_s: 88,
    });

    render(<App />);

    const badge = await waitFor(() => screen.getByTestId("doorpad-identity"));
    expect(badge.textContent).toContain("Tiger");
  });

  it("offers a named check-in for the recognised person", async () => {
    mockSnapshot({
      state: "IDLE",
      display_name: "Tiger",
      profile_id: "sunrise",
      attributed_to: "Tiger",
      identity_expires_in_s: 88,
    });

    render(<App />);
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
    fireEvent.click(document.getElementById("btn-checkin") as HTMLElement);

    expect(screen.getByText("Check in as Tiger")).toBeTruthy();
  });
});

describe("doorpad tile hints", () => {
  it("says what each tile does, not just what it is", async () => {
    mockSnapshot({ state: "IDLE", display_name: null });

    render(<App />);
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());

    // The labels alone ("Guestbook", "Vote in Poll") mean nothing to a visitor.
    expect(screen.getByText("Let them know you're here")).toBeTruthy();
    expect(screen.getByText("Leave a note on the big screen")).toBeTruthy();
    expect(screen.getByText("Answer today's question")).toBeTruthy();
  });

  it("presents enrollment as an invitation rather than one tile of nine", async () => {
    mockSnapshot({ state: "IDLE", display_name: null });

    render(<App />);
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());

    const enroll = document.getElementById("btn-enroll");
    expect(enroll).toBeTruthy();
    // Emphasis comes from its own accent class, not a second `primary` variant: two
    // bright tiles left nothing reading as *the* action, so Ring Bell keeps that role.
    expect(enroll?.className).toContain("doorpad-tile--enroll");
    expect(enroll?.className).not.toContain("db-big-button--primary");
    const ring = document.getElementById("btn-ring");
    expect(ring?.className).toContain("db-big-button--primary");
    expect(screen.getByText(/greets you by name/)).toBeTruthy();
  });
});
