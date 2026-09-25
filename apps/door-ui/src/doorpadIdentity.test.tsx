// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { FakeWebSocket, sessionDelta } from "./test/fakeWebSocket";

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

/**
 * The `/ws` path. door-api sends the session transition as a delta and the merged
 * identity as a snapshot; the two say different things about who is held, and the
 * doorpad has to keep them apart.
 */

/** `GET /session` answers with each session in turn, then repeats the last one. */
function mockSessionSequence(sessions: Array<Record<string, unknown>>) {
  let sessionCalls = 0;
  const fetchMock = vi.fn<(...args: unknown[]) => Promise<Response>>(async (...args) => {
    const url = String(args[0] ?? "");
    if (url.includes("/doorpad/activity")) {
      return { ok: true, status: 202, json: async () => ({}) } as Response;
    }
    if (url.endsWith("/session")) {
      const session = sessions[Math.min(sessionCalls, sessions.length - 1)];
      sessionCalls += 1;
      return { ok: true, status: 200, json: async () => ({ session, config: {} }) } as Response;
    }
    return { ok: true, status: 200, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, sessionCalls: () => sessionCalls };
}

const HELD_TIGER = {
  state: "APPROACH_DETECTED",
  session_id: "ses-approach",
  display_name: "Tiger",
  profile_id: "sunrise",
  accent_color: "#00d7a3",
  attributed_to: "Tiger",
  identity_expires_in_s: 104,
};

const NOBODY_HELD = {
  state: "IDLE",
  session_id: null,
  display_name: null,
  profile_id: null,
  accent_color: null,
  attributed_to: null,
  identity_expires_in_s: 0,
};

function idleDelta(displayName: string | null) {
  return sessionDelta("APPROACH_DETECTED", "IDLE", {
    trigger: "timeout:approach",
    display_name: displayName,
  });
}

describe("doorpad identity over /ws (ADR-0020)", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
  });

  it("keeps the badge through an IDLE delta and clears it only on the server's snapshot", async () => {
    // The reported regression: a recognised person walked up, the badge appeared, and ten
    // seconds later the approach timer's IDLE delta wiped it — while door-api went on
    // attributing every write to them for up to two minutes.
    const { sessionCalls } = mockSessionSequence([HELD_TIGER, { ...HELD_TIGER, state: "IDLE" }]);

    render(<App />);
    await waitFor(() => expect(screen.getByTestId("doorpad-identity").textContent).toContain("Tiger"));
    const ws = FakeWebSocket.instances[0];
    expect(ws).toBeTruthy();
    act(() => ws.open());
    const callsBefore = sessionCalls();

    act(() => ws.emit(idleDelta("Tiger")));

    // The delta triggers a re-read of the merged snapshot rather than a local guess.
    await waitFor(() => expect(sessionCalls()).toBeGreaterThan(callsBefore));
    expect(screen.getByTestId("doorpad-identity").textContent).toContain("Tiger");
    // The check-in offer and the badge read the same value, so they agree.
    fireEvent.click(document.getElementById("btn-checkin") as HTMLElement);
    expect(screen.getByText("Check in as Tiger")).toBeTruthy();
    fireEvent.click(screen.getByText("Back"));

    // door-api pushes a snapshot when the holder lapses: that, and only that, clears it.
    act(() => ws.emit({ type: "snapshot", state: NOBODY_HELD }));

    await waitFor(() => expect(screen.queryByTestId("doorpad-identity")).toBeNull());
    fireEvent.click(document.getElementById("btn-checkin") as HTMLElement);
    expect(screen.queryByText("Check in as Tiger")).toBeNull();
    fireEvent.click(screen.getByText("Back"));
    // And the E-23 notice stops naming them at the same moment as the badge.
    fireEvent.click(document.getElementById("btn-guestbook") as HTMLElement);
    expect(screen.queryByTestId("attribution-notice")).toBeNull();
  });

  it("does not clear the badge on an IDLE delta that carries no name", async () => {
    // A delta's display_name is the session machine's copy, not the holder's; even a null
    // there is not evidence that the holder let go. Only the snapshot is.
    const { sessionCalls } = mockSessionSequence([HELD_TIGER, { ...HELD_TIGER, state: "IDLE" }]);

    render(<App />);
    await waitFor(() => expect(screen.getByTestId("doorpad-identity")).toBeTruthy());
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    const callsBefore = sessionCalls();

    act(() => ws.emit(idleDelta(null)));

    await waitFor(() => expect(sessionCalls()).toBeGreaterThan(callsBefore));
    expect(screen.getByTestId("doorpad-identity").textContent).toContain("Tiger");
  });

  it("asks the server again when the local identity countdown runs out", async () => {
    // The countdown is armed from identity_expires_in_s. When it fires the doorpad asks
    // rather than assumes: a touch re-arms the window server-side, so a stale timer must
    // not forget someone who is still tapping. Here the server has let go, so it clears.
    // Real time, deliberately: the countdown is armed inside the snapshot handler, before
    // any fake clock could be installed, so a half-second window is waited out for real.
    const { sessionCalls } = mockSessionSequence([
      { ...HELD_TIGER, identity_expires_in_s: 0.5 },
      NOBODY_HELD,
    ]);

    render(<App />);
    await waitFor(() => expect(screen.getByTestId("doorpad-identity")).toBeTruthy());
    const callsBefore = sessionCalls();

    await waitFor(() => expect(sessionCalls()).toBeGreaterThan(callsBefore), { timeout: 2_500 });
    await waitFor(() => expect(screen.queryByTestId("doorpad-identity")).toBeNull());
  });
});
