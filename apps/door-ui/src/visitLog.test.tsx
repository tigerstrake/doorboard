// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { AdminVisitLogPanel } from "./AdminVisitLogPanel";

/**
 * Arrival-log admin panel (ADR-0018 §1).
 *
 * The gap this closes: the endpoints shipped without an interface, so the only way
 * to read or purge arrival history was curl — for data the consent statement
 * promises is admin-visible and deletable on request.
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const VISITS = [
  {
    visit_id: "vst_1",
    person_id: "prs_tiger",
    display_name: "Tiger",
    arrived_at: "2026-08-06T18:00:00Z",
    last_seen_at: "2026-08-06T18:15:00Z",
  },
];
const COUNTS = [
  {
    person_id: "prs_tiger",
    display_name: "Tiger",
    visits: 3,
    last_seen_at: "2026-08-06T18:15:00Z",
  },
];

function mockRoutes(overrides: Record<string, unknown> = {}) {
  const calls: Array<{ url: string; method: string }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown, init?: RequestInit) => {
      const url = typeof input === "string" ? input : String(input);
      calls.push({ url, method: (init?.method ?? "GET").toUpperCase() });
      const body =
        url.includes("/visits/counts") ? (overrides.counts ?? COUNTS)
        : url.includes("/visits/purge") ? { deleted: 3 }
        : url.includes("/visits") ? (overrides.visits ?? VISITS)
        : {};
      return { ok: true, status: 200, json: async () => body } as Response;
    })
  );
  return calls;
}

describe("AdminVisitLogPanel", () => {
  it("shows arrivals and per-person totals", async () => {
    mockRoutes();
    render(<AdminVisitLogPanel token="admin-token" />);

    await waitFor(() => expect(screen.getByText("Recent arrivals (1)")).toBeTruthy());
    // Once in the per-person totals, once in the arrivals list.
    expect(screen.getAllByText("Tiger")).toHaveLength(2);
    expect(screen.getByText(/3 arrivals/)).toBeTruthy();
    // 18:00 -> 18:15 is a 15-minute stay, derived rather than shown as two stamps.
    expect(screen.getByText("15 min")).toBeTruthy();
  });

  it("sends the admin token on every read", async () => {
    mockRoutes();
    render(<AdminVisitLogPanel token="admin-token" />);
    await waitFor(() => expect(screen.getByText("Recent arrivals (1)")).toBeTruthy());

    const fetchMock = globalThis.fetch as unknown as { mock: { calls: unknown[][] } };
    for (const call of fetchMock.mock.calls) {
      const init = call[1] as RequestInit | undefined;
      const headers = init?.headers as Record<string, string> | undefined;
      expect(headers?.Authorization).toBe("Bearer admin-token");
    }
  });

  it("explains an empty log in terms of the consent gate, not as an error", async () => {
    mockRoutes({ visits: [], counts: [] });
    render(<AdminVisitLogPanel token="admin-token" />);

    await waitFor(() => expect(screen.getByText(/No arrivals recorded yet/)).toBeTruthy());
    expect(screen.getByText(/agreed to a greeting, not to being logged/)).toBeTruthy();
  });

  it("requires confirmation before deleting, and says templates survive", async () => {
    mockRoutes();
    render(<AdminVisitLogPanel token="admin-token" />);
    await waitFor(() => expect(screen.getByText("Recent arrivals (1)")).toBeTruthy());

    fireEvent.click(screen.getByText("Delete the entire arrival log"));

    expect(screen.getByText(/Delete all 1 recorded arrivals\?/)).toBeTruthy();
    // The distinction that matters: forgetting arrivals is not unenrolling.
    expect(screen.getByText(/Face templates are/)).toBeTruthy();
    expect(screen.getByText(/still be recognised and greeted/)).toBeTruthy();
  });

  it("cancelling the confirm dialog sends no request", async () => {
    const calls = mockRoutes();
    render(<AdminVisitLogPanel token="admin-token" />);
    await waitFor(() => expect(screen.getByText("Recent arrivals (1)")).toBeTruthy());

    fireEvent.click(screen.getByText("Delete the entire arrival log"));
    fireEvent.click(screen.getByText("Cancel"));

    expect(screen.queryByText(/Delete all/)).toBeNull();
    expect(calls.some((c) => c.url.includes("/visits/purge"))).toBe(false);
  });

  it("purges everything when confirmed", async () => {
    const calls = mockRoutes();
    render(<AdminVisitLogPanel token="admin-token" />);
    await waitFor(() => expect(screen.getByText("Recent arrivals (1)")).toBeTruthy());

    fireEvent.click(screen.getByText("Delete the entire arrival log"));
    fireEvent.click(screen.getByText("Delete permanently"));

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/visits/purge") && c.method === "POST")).toBe(true)
    );
    // No person_id means "everyone".
    const purge = calls.find((c) => c.url.includes("/visits/purge"));
    expect(purge?.url).not.toContain("person_id");
  });

  it("purges one person by id", async () => {
    const calls = mockRoutes();
    render(<AdminVisitLogPanel token="admin-token" />);
    await waitFor(() => expect(screen.getByText("Recent arrivals (1)")).toBeTruthy());

    fireEvent.click(screen.getByText("Forget arrivals"));
    fireEvent.click(screen.getByText("Delete permanently"));

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("person_id=prs_tiger"))).toBe(true)
    );
  });

  it("survives an unexpected payload rather than blanking the admin screen", async () => {
    mockRoutes({ visits: { not: "an array" }, counts: null });
    render(<AdminVisitLogPanel token="admin-token" />);
    await waitFor(() => expect(screen.getByText("Arrival log")).toBeTruthy());
    expect(screen.getByText(/No arrivals recorded yet/)).toBeTruthy();
  });

  it("renders a hostile display name inertly", async () => {
    const hostile = "<script>alert('xss')</script>";
    mockRoutes({ visits: [{ ...VISITS[0], display_name: hostile }], counts: [] });
    render(<AdminVisitLogPanel token="admin-token" />);

    await waitFor(() => expect(screen.getByText(hostile)).toBeTruthy());
    expect(screen.getByText("Recent arrivals (1)")).toBeTruthy();
    expect(document.querySelector("script")).toBeNull();
  });
});
