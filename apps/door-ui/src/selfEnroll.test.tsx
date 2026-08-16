// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { fireEvent, render, screen, cleanup, waitFor } from "@testing-library/react";

/**
 * T-312 / ADR-0019 — "Enroll My Face" on the doorpad.
 *
 * The button is the easy half. What these pin is the refusal path: a visitor who is
 * turned away is standing at a door deciding whether to knock, so every reason the
 * Pi can give has to become a sentence rather than a spinner that stops.
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
  window.localStorage.clear();
  window.history.pushState(null, "", "/");
});

async function loadApp() {
  const mod = await import("./App");
  return mod.App;
}

const INVITE = {
  invite_id: "inv_abc",
  url: "https://relay.example.test/e/inv_abc.secret#k=fp",
  expires_at: "2026-08-16T22:16:00+00:00",
};

/** Stub every endpoint the doorpad touches, with the invite call configurable. */
function stubFetch(invite: { status: number; body: unknown }) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/doorpad/enroll-invite")) {
      return {
        ok: invite.status < 400,
        status: invite.status,
        json: async () => invite.body,
      } as Response;
    }
    let body: unknown = { session: { state: "IDLE" }, config: {} };
    if (url.includes("/guestbook")) body = { entries: [] };
    else if (url.includes("/polls/current")) body = { poll: null };
    else if (url.includes("/visitor-token")) body = { token: "t", url: "u" };
    void init;
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function openDoorpad(invite: { status: number; body: unknown }) {
  window.history.pushState(null, "", "/doorpad");
  const fetchMock = stubFetch(invite);
  const App = await loadApp();
  render(<App />);
  await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());
  return fetchMock;
}

describe("Self-service enrollment from the doorpad", () => {
  it("offers an obvious button and shows a scannable code", async () => {
    const fetchMock = await openDoorpad({ status: 201, body: INVITE });

    fireEvent.click(screen.getByText("Enroll My Face"));

    await waitFor(() => expect(screen.getByTestId("qr-placeholder")).toBeTruthy());
    const call = fetchMock.mock.calls.find(([url]) =>
      String(url).includes("/doorpad/enroll-invite")
    );
    expect(call).toBeTruthy();
    expect((call?.[1] as RequestInit).method).toBe("POST");
    // ADR-0019: standing at the door is the authorization; no token is sent.
    expect((call?.[1] as RequestInit).headers).toBeUndefined();
  });

  it("does not print the invite URL on a screen in a hallway", async () => {
    await openDoorpad({ status: 201, body: INVITE });

    fireEvent.click(screen.getByText("Enroll My Face"));

    await waitFor(() => expect(screen.getByTestId("qr-placeholder")).toBeTruthy());
    // The URL is a single-use secret. The code carries it; the screen should not
    // spell it out for whoever else is in the corridor.
    expect(screen.queryByTestId("qr-placeholder-url")).toBeNull();
    expect(screen.queryByText(/inv_abc\.secret/)).toBeNull();
  });

  it("explains the consent and deletion story before anyone scans", async () => {
    await openDoorpad({ status: 201, body: INVITE });

    fireEvent.click(screen.getByText("Enroll My Face"));

    await waitFor(() => expect(screen.getByTestId("qr-placeholder")).toBeTruthy());
    expect(screen.getByText(/deleted once your face is learned/)).toBeTruthy();
    expect(screen.getByText(/works once/)).toBeTruthy();
  });

  it.each([
    ["rate_limited", 429, /reopens within the hour/],
    ["door_full", 409, /as many saved faces as it holds/],
    ["disabled", 409, /switched off at this door/],
  ])("says why it is closed for %s", async (reason, status, copy) => {
    await openDoorpad({
      status,
      body: { detail: { error: "self_enroll_closed", reason } },
    });

    fireEvent.click(screen.getByText("Enroll My Face"));

    await waitFor(() => expect(screen.getByTestId("enroll-closed")).toBeTruthy());
    expect(screen.getByText(copy)).toBeTruthy();
    expect(screen.queryByTestId("qr-placeholder")).toBeNull();
  });

  it("explains a locked enrollment volume in a visitor's terms", async () => {
    await openDoorpad({ status: 503, body: { detail: "encrypted enrollment storage is locked" } });

    fireEvent.click(screen.getByText("Enroll My Face"));

    await waitFor(() => expect(screen.getByTestId("enroll-closed")).toBeTruthy());
    expect(screen.getByText(/cannot reach its secure storage/)).toBeTruthy();
  });

  it("falls back to a general sentence for a reason it has never heard of", async () => {
    await openDoorpad({
      status: 409,
      body: { detail: { error: "self_enroll_closed", reason: "invented_later" } },
    });

    fireEvent.click(screen.getByText("Enroll My Face"));

    await waitFor(() => expect(screen.getByTestId("enroll-closed")).toBeTruthy());
    expect(screen.getByText(/not available at this door right now/)).toBeTruthy();
  });

  it("survives a refusal whose body is not JSON", async () => {
    window.history.pushState(null, "", "/doorpad");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/doorpad/enroll-invite")) {
          return {
            ok: false,
            status: 409,
            json: async () => {
              throw new Error("not json");
            },
          } as unknown as Response;
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({ session: { state: "IDLE" }, config: {} }),
        } as Response;
      })
    );
    const App = await loadApp();
    render(<App />);
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());

    fireEvent.click(screen.getByText("Enroll My Face"));

    await waitFor(() => expect(screen.getByTestId("enroll-closed")).toBeTruthy());
  });

  it("offers a fresh code, since an invite is single use", async () => {
    const fetchMock = await openDoorpad({ status: 201, body: INVITE });

    fireEvent.click(screen.getByText("Enroll My Face"));
    await waitFor(() => expect(screen.getByTestId("qr-placeholder")).toBeTruthy());
    fireEvent.click(screen.getByText("New Code"));

    await waitFor(() => {
      const mints = fetchMock.mock.calls.filter(([url]) =>
        String(url).includes("/doorpad/enroll-invite")
      );
      expect(mints.length).toBe(2);
    });
  });

  it("says the door is unreachable rather than hanging", async () => {
    window.history.pushState(null, "", "/doorpad");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/doorpad/enroll-invite")) throw new Error("network down");
        return {
          ok: true,
          status: 200,
          json: async () => ({ session: { state: "IDLE" }, config: {} }),
        } as Response;
      })
    );
    const App = await loadApp();
    render(<App />);
    await waitFor(() => expect(screen.getByText("Room 304 DoorPad")).toBeTruthy());

    fireEvent.click(screen.getByText("Enroll My Face"));

    await waitFor(() => expect(screen.getByTestId("enroll-failed")).toBeTruthy());
    expect(screen.getByText(/could not be reached/)).toBeTruthy();
  });
});
