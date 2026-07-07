// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { AdminEnrollmentPanel } from "./AdminEnrollmentPanel";

const HOSTILE = "<script>alert('xss')</script>";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
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

describe("AdminEnrollmentPanel renders enrolled people and handles consent statement", () => {
  beforeEach(() => {
    window.localStorage.setItem("doorboard_admin_social_token", "test-token");
  });

  it("renders hostile display name safely and inertly", async () => {
    mockFetchSequence([
      // GET /people
      {
        body: [
          {
            person_id: "prs_test",
            display_name: HOSTILE,
            consent_version: "v1",
            consent_at: "2026-07-07T12:00:00Z",
            created_at: "2026-07-07T12:00:00Z",
            profile_id: "blue_wave",
            color: "#0000ff",
            sound: null,
          },
        ],
      },
      // GET /consent
      {
        body: {
          text: "# Face-recognition consent statement\n\nVersion: v1\nConsent statement verbatim text.",
          version: "v1",
        },
      },
      // GET /health
      {
        body: {
          privacy_enabled: false,
        },
      },
    ]);

    render(<AdminEnrollmentPanel />);

    // Should display the name verbatim in text
    await waitFor(() => expect(screen.getByText(HOSTILE)).toBeTruthy());
    // Should NOT create a script element executing it
    expect(document.querySelector("script")).toBeNull();
  });

  it("can toggle privacy mode on click", async () => {
    const fetchMock = mockFetchSequence([
      // GET /people
      { body: [] },
      // GET /consent
      { body: { text: "Consent Statement", version: "v1" } },
      // GET /health
      { body: { privacy_enabled: false } },
      // POST /privacy-mode (on toggle check)
      { body: { enabled: true } },
    ]);

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
