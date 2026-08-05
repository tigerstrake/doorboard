// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { AdminEnrollmentPanel } from "./AdminEnrollmentPanel";

const HOSTILE = "<script>alert('xss')</script>";

// The QR itself comes from ui-kit's QRPlaceholder, which renders a data URL and
// so needs no canvas in jsdom.

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  window.localStorage.clear();
});

interface MockResponse {
  status?: number;
  body: unknown;
}

/**
 * Route mocked responses by URL rather than call order.
 *
 * Order-based mocking made this suite brittle: any component that added a fetch
 * shifted every later response by one. Matching on the path means a test only
 * describes the endpoints it cares about, and unlisted endpoints get a benign
 * default instead of another endpoint's body.
 */
function mockFetchRoutes(routes: Record<string, MockResponse>, fallback: MockResponse = { body: {} }) {
  const fetchMock = vi.fn(async (input: unknown, init?: RequestInit) => {
    const url = typeof input === "string" ? input : String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    // A key may be prefixed with a method ("POST /invites") to distinguish verbs
    // on one path; a bare path matches any verb.
    const candidates = Object.keys(routes)
      // Longest pattern first so "/invites" does not shadow a more specific path.
      .sort((a, b) => b.length - a.length);
    const match =
      candidates.find((pattern) => {
        const [verb, path] = pattern.split(" ");
        return path !== undefined && verb === method && url.includes(path);
      }) ?? candidates.find((pattern) => !pattern.includes(" ") && url.includes(pattern));
    const resp = match ? routes[match]! : fallback;
    return {
      ok: (resp.status ?? 200) < 400,
      status: resp.status ?? 200,
      json: async () => resp.body,
    } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The endpoints AdminEnrollmentPanel and its remote-enrollment section load. */
function defaultRoutes(overrides: Record<string, MockResponse> = {}): Record<string, MockResponse> {
  return {
    "/people": { body: [] },
    "/consent": { body: { text: "Consent Statement", version: "v2" } },
    "/health": { body: { privacy_enabled: false } },
    "/invites": { body: [] },
    "/relay-status": { body: { configured: false, status: "disabled" } },
    ...overrides,
  };
}

describe("AdminEnrollmentPanel renders enrolled people and handles consent statement", () => {
  beforeEach(() => {
    window.localStorage.setItem("doorboard_admin_social_token", "test-token");
  });

  it("renders hostile display name safely and inertly", async () => {
    mockFetchRoutes(
      defaultRoutes({
        "/people": {
          body: [
            {
              person_id: "prs_test",
              display_name: HOSTILE,
              consent_version: "v2",
              consent_at: "2026-07-07T12:00:00Z",
              created_at: "2026-07-07T12:00:00Z",
              profile_id: "blue_wave",
              color: "#0000ff",
              sound: null,
            },
          ],
        },
      })
    );

    render(<AdminEnrollmentPanel />);

    // Should display the name verbatim in text
    await waitFor(() => expect(screen.getByText(HOSTILE)).toBeTruthy());
    // Should NOT create a script element executing it
    expect(document.querySelector("script")).toBeNull();
  });

  it("can toggle privacy mode on click", async () => {
    const fetchMock = mockFetchRoutes(
      defaultRoutes({ "/privacy-mode": { body: { enabled: true } } })
    );

    render(<AdminEnrollmentPanel />);

    await waitFor(() => expect(screen.getByLabelText(/Privacy Mode:/i)).toBeTruthy());
    const toggle = screen.getByLabelText(/Privacy Mode:/i) as HTMLInputElement;
    expect(toggle.checked).toBe(false);

    fireEvent.click(toggle);

    await waitFor(() => {
      // Check that POST /privacy-mode was called
      const calls = fetchMock.mock.calls;
      const hasPrivacyPost = (calls as unknown[][]).some(
        (call) => typeof call[0] === "string" && call[0].includes("/privacy-mode")
      );
      expect(hasPrivacyPost).toBe(true);
    });
  });
});

describe("remote enrollment (ADR-0016) in the admin panel", () => {
  beforeEach(() => {
    window.localStorage.setItem("doorboard_admin_social_token", "test-token");
  });

  it("warns when no relay is configured and still allows at-door enrollment", async () => {
    mockFetchRoutes(defaultRoutes());
    render(<AdminEnrollmentPanel />);

    await waitFor(() => expect(screen.getByText(/Enroll from a phone/i)).toBeTruthy());
    expect(screen.getByText(/No relay is configured/i)).toBeTruthy();
    // The at-door path is unaffected by a missing relay.
    expect((screen.getByText("Enroll New Face") as HTMLButtonElement).disabled).toBe(false);
  });

  it("shows relay reachability from the door's own view of it", async () => {
    mockFetchRoutes(
      defaultRoutes({
        "/relay-status": {
          body: { configured: true, status: "degraded", last_error: "RelayTransportError" },
        },
      })
    );
    render(<AdminEnrollmentPanel />);

    await waitFor(() => expect(screen.getByText(/Relay unreachable/i)).toBeTruthy());
    expect(screen.getByText(/RelayTransportError/)).toBeTruthy();
  });

  it("renders a QR code and the single-use link after minting an invite", async () => {
    const url = "https://enroll.example.test/e/inv_aaaaaaaaaaaaaaaaaaaaaa.c2VjcmV0#k=Zmluz2VycHJpbnQxMg";
    mockFetchRoutes(
      defaultRoutes({
        "/relay-status": { body: { configured: true, status: "ok" } },
        "POST /invites": {
          body: {
            invite_id: "inv_aaaaaaaaaaaaaaaaaaaaaa",
            url,
            expires_at: "2026-08-05T13:00:00Z",
            max_images: 5,
            relay_configured: true,
            door_key_fingerprint: "Zmluz2VycHJpbnQxMg",
          },
        },
      })
    );

    render(<AdminEnrollmentPanel />);
    await waitFor(() => expect(screen.getByText("Create QR invite")).toBeTruthy());
    fireEvent.click(screen.getByText("Create QR invite"));

    await waitFor(() => expect(screen.getByText(/Scan this with the phone/i)).toBeTruthy());
    // The QR encodes the invite URL itself — including the #k= fingerprint, which
    // is what lets the phone detect a substituted door key (E-10).
    expect(screen.getByTestId("qr-placeholder-url").textContent).toBe(url);
    await waitFor(() =>
      expect(screen.getByRole("img", { name: /Enrollment invitation QR code/i })).toBeTruthy()
    );
    // The admin must be told the link is unrecoverable, since only its hash is kept.
    expect(screen.getByText(/shown only once/i)).toBeTruthy();
  });

  it("blocks minting invites while privacy mode is on", async () => {
    mockFetchRoutes(
      defaultRoutes({
        "/health": { body: { privacy_enabled: true } },
        "/relay-status": { body: { configured: true, status: "ok" } },
      })
    );
    render(<AdminEnrollmentPanel />);

    await waitFor(() => expect(screen.getByText("Create QR invite")).toBeTruthy());
    expect((screen.getByText("Create QR invite") as HTMLButtonElement).disabled).toBe(true);
  });

  it("lists open invites with a revoke control", async () => {
    mockFetchRoutes(
      defaultRoutes({
        "/relay-status": { body: { configured: true, status: "ok" } },
        "/invites": {
          body: [
            {
              invite_id: "inv_aaaaaaaaaaaaaaaaaaaaaa",
              label: "Tiger's phone",
              max_images: 5,
              created_at: "2026-08-05T12:00:00Z",
              expires_at: "2026-08-05T13:00:00Z",
              consumed_at: null,
              revoked_at: null,
              person_id: null,
              status: "open",
            },
          ],
        },
      })
    );

    render(<AdminEnrollmentPanel />);
    await waitFor(() => expect(screen.getByText("Tiger's phone")).toBeTruthy());
    expect(screen.getByText("Open invites (1)")).toBeTruthy();
    expect(screen.getByText("Revoke")).toBeTruthy();
  });

  it("never renders an invite secret for a past invite", async () => {
    mockFetchRoutes(
      defaultRoutes({
        "/relay-status": { body: { configured: true, status: "ok" } },
        "/invites": {
          body: [
            {
              invite_id: "inv_bbbbbbbbbbbbbbbbbbbbbb",
              label: "Old phone",
              max_images: 5,
              created_at: "2026-08-05T10:00:00Z",
              expires_at: "2026-08-05T11:00:00Z",
              consumed_at: "2026-08-05T10:05:00Z",
              revoked_at: null,
              person_id: "prs_test",
              status: "consumed",
            },
          ],
        },
      })
    );

    render(<AdminEnrollmentPanel />);
    await waitFor(() => expect(screen.getByText(/Past invites \(1\)/i)).toBeTruthy());
    // Consumed invites are history: no link, nothing rescannable.
    expect(screen.queryByText(/Scan this with the phone/i)).toBeNull();
    expect(screen.queryByText(/#k=/)).toBeNull();
  });
});
