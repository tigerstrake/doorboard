// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { RevealPage } from "./RevealPage";
import type { VisitorCollage } from "./wallboardChannels";

const COLLAGE: VisitorCollage = {
  stats: {
    total_checkins: 12,
    checkins_this_year: 9,
    unique_visitors: 5,
    distinct_visitors: 3,
    guest_count: 2,
    most_frequent: { label: "Alex", count: 4 },
    first_checkin_at: "2026-01-02T10:00:00Z",
    most_recent_checkin_at: "2026-06-12T18:30:00Z",
  },
  photos: [
    { recording_id: "rec_1", thumbnail_path: null, label: "Alex", created_at: "2026-06-12T18:30:00Z" },
    { recording_id: "rec_2", thumbnail_path: null, label: "Sam", created_at: "2026-05-10T12:00:00Z" },
  ],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("RevealPage (owner-only secret reveal)", () => {
  it("reads the token from the URL hash and renders the collage on success", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => COLLAGE,
    })) as unknown as typeof fetch;
    vi.stubGlobal("fetch", fetchMock);
    window.location.hash = "#super-secret-owner-token";

    render(<RevealPage />);

    // The celebratory reveal appears once the fetch resolves.
    expect(await screen.findByText(/Everyone who stopped by this year/i)).toBeTruthy();
    // The check-in photos (with labels) render in the collage grid.
    const grid = document.querySelector(".visitor-collage-grid") as HTMLElement;
    expect(within(grid).getByText("Alex")).toBeTruthy();
    expect(within(grid).getByText("Sam")).toBeTruthy();
    // The celebratory fun-stats are shown.
    expect(screen.getByText("total visits")).toBeTruthy();
    expect(screen.getByText("first visit")).toBeTruthy();

    // The token was passed as a Bearer header to the admin endpoint — never a
    // query string.
    const [url, init] = (fetchMock as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(String(url)).toContain("/admin/visitor-collage");
    expect(String(url)).not.toContain("super-secret-owner-token");
    expect((init as RequestInit).headers).toMatchObject({
      Authorization: "Bearer super-secret-owner-token",
    });
  });

  it("shows a neutral empty state when the token is missing (no data leaked)", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => COLLAGE }));
    vi.stubGlobal("fetch", fetchMock);
    window.location.hash = "";

    render(<RevealPage />);

    expect(await screen.findByText(/Nothing to see here/i)).toBeTruthy();
    // No collage data is fetched or rendered when there is no token.
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByText("Alex")).toBeNull();
    expect(screen.queryByText("total visits")).toBeNull();
  });

  it("shows the same neutral empty state on an invalid token (401)", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 401,
      json: async () => ({ detail: "invalid admin token" }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    window.location.hash = "#wrong-token";

    render(<RevealPage />);

    expect(await screen.findByText(/Nothing to see here/i)).toBeTruthy();
    // Nothing about the collage is revealed on a rejected token.
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByText("Alex")).toBeNull();
    expect(screen.queryByText("total visits")).toBeNull();
  });
});
