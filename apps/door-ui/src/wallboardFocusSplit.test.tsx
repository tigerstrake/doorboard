// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type {
  AmbientAircraftSummaryPayload,
  AmbientSatellitePassPayload,
} from "@doorboard/contracts";
import type { Poll, PollResultRow } from "./socialApi";

// The aircraft focus panel mounts a Leaflet map, which needs a real DOM/canvas
// + network tiles. Mock Leaflet (and its CSS) so this suite exercises the split
// layout in jsdom without the real library.
vi.mock("leaflet", () => {
  const chainable = () => {
    const api: Record<string, unknown> = {};
    for (const method of ["setView", "addTo", "on", "clearLayers", "remove", "invalidateSize", "fitBounds"]) {
      api[method] = () => api;
    }
    return api;
  };
  const L = {
    map: () => chainable(),
    tileLayer: () => chainable(),
    layerGroup: () => chainable(),
    marker: () => chainable(),
    divIcon: () => ({}),
    latLngBounds: () => ({}),
  };
  return { default: L, ...L };
});
vi.mock("leaflet/dist/leaflet.css", () => ({}));

import { WallboardFocusSplit } from "./wallboardChannels";

afterEach(() => cleanup());

const AIRCRAFT: AmbientAircraftSummaryPayload = {
  as_of: "2026-07-20T12:00:00Z",
  nearby: [
    { callsign: "UAL123", altitude_ft: 32000, distance_km: 8, heading: 270 },
    { callsign: "SWA456", altitude_ft: 18000, distance_km: 14, heading: 90 },
  ],
};

const SATELLITE: AmbientSatellitePassPayload = {
  satellite: "ISS (ZARYA)",
  rise_at: "2026-07-20T21:14:00Z",
  max_elevation_deg: 72,
  direction: "NW",
  visible: true,
};

const POLL: Poll = {
  id: "poll-1",
  question: "Best late-night study snack?",
  status: "open",
  created_at: "2026-07-20T00:00:00Z",
  closed_at: null,
  options: [
    { id: "a", text: "Instant noodles" },
    { id: "b", text: "Trail mix" },
  ],
};

const POLL_RESULTS: PollResultRow[] = [
  { option_id: "a", text: "Instant noodles", votes: 7 },
  { option_id: "b", text: "Trail mix", votes: 3 },
];

const EMPTY_AMBIENT = {
  aircraft: null,
  birds: null,
  birdCollageUrl: "",
  satellite: null,
  printer: null,
  food: null,
  scoreboard: null,
} as const;

function renderSplit(
  overrides: Partial<React.ComponentProps<typeof WallboardFocusSplit>> = {},
) {
  const props: React.ComponentProps<typeof WallboardFocusSplit> = {
    channel: "aircraft",
    poll: null,
    pollResults: null,
    guestbookEntries: [],
    moments: [],
    ambient: { ...EMPTY_AMBIENT, aircraft: AIRCRAFT },
    secondary: (
      <>
        <div data-testid="rail-tile-a">Presence tile</div>
        <div data-testid="rail-tile-b">Bird tile</div>
      </>
    ),
    onReturnAmbient: () => {},
    ...overrides,
  };
  return render(<WallboardFocusSplit {...props} />);
}

describe("WallboardFocusSplit (focused-tile split layout)", () => {
  it("renders the expanded focus panel AND the secondary rail side by side", () => {
    renderSplit();

    const panel = screen.getByTestId("wallboard-focus-panel");
    const rail = screen.getByTestId("wallboard-focus-rail");
    expect(panel).toBeTruthy();
    expect(rail).toBeTruthy();

    // Expanded panel shows the rich Flights view: the live map headline plus a
    // per-plane detail card (incl. the heading the shrunken ambient tile omits).
    expect(within(panel).getByTestId("flights-map")).toBeTruthy();
    expect(within(panel).getByText("UAL123")).toBeTruthy();
    expect(within(panel).getByText("270°")).toBeTruthy();

    // The other tiles stay visible around it in the rail.
    expect(within(rail).getByTestId("rail-tile-a")).toBeTruthy();
    expect(within(rail).getByTestId("rail-tile-b")).toBeTruthy();
  });

  it("keeps the 'Ambient grid' exit affordance and fires it on tap", () => {
    const onReturnAmbient = vi.fn();
    renderSplit({ onReturnAmbient });
    fireEvent.click(screen.getByRole("button", { name: /ambient grid/i }));
    expect(onReturnAmbient).toHaveBeenCalledTimes(1);
  });

  it("lays out the satellite pass details large in the panel", () => {
    renderSplit({ channel: "satellite", ambient: { ...EMPTY_AMBIENT, satellite: SATELLITE } });
    const panel = screen.getByTestId("wallboard-focus-panel");
    expect(within(panel).getByText("ISS (ZARYA)")).toBeTruthy();
    expect(within(panel).getByText(/NW · 72° max/)).toBeTruthy();
  });

  it("renders the sky-compass aimed at the satellite rise direction", () => {
    renderSplit({ channel: "satellite", ambient: { ...EMPTY_AMBIENT, satellite: SATELLITE } });
    const panel = screen.getByTestId("wallboard-focus-panel");
    const compass = within(panel).getByTestId("sky-compass");
    expect(compass.getAttribute("aria-label")).toMatch(/NW/);
    // Max-elevation readout is present as its own large stat.
    expect(within(panel).getByText("72°")).toBeTruthy();
  });

  it("lays out the poll question with per-option vote bars and a highlighted leader", () => {
    renderSplit({ channel: "poll", poll: POLL, pollResults: POLL_RESULTS });
    const panel = screen.getByTestId("wallboard-focus-panel");
    expect(within(panel).getByText(/Best late-night study snack/)).toBeTruthy();
    expect(within(panel).getByText("Instant noodles")).toBeTruthy();
    expect(within(panel).getByText("Trail mix")).toBeTruthy();

    // 7 of 10 votes → 70% bar for the leader, 30% for the runner-up.
    const leaderBar = within(panel).getByRole("progressbar", { name: "Instant noodles" });
    expect(leaderBar.getAttribute("aria-valuenow")).toBe("70");
    const otherBar = within(panel).getByRole("progressbar", { name: "Trail mix" });
    expect(otherBar.getAttribute("aria-valuenow")).toBe("30");

    // Total tally is surfaced.
    expect(within(panel).getByText(/10 total votes/)).toBeTruthy();
  });

  it("shows a large, graceful empty state when the poll channel has no poll", () => {
    renderSplit({ channel: "poll", poll: null });
    const panel = screen.getByTestId("wallboard-focus-panel");
    expect(within(panel).getByTestId("focus-empty-state")).toBeTruthy();
    expect(within(panel).getByText(/No poll is running/i)).toBeTruthy();
  });

  it("renders a graceful '…unavailable' panel when the channel has no data yet", () => {
    renderSplit({ channel: "satellite", ambient: EMPTY_AMBIENT });
    const panel = screen.getByTestId("wallboard-focus-panel");
    expect(within(panel).getByText(/unavailable/i)).toBeTruthy();
    // Rail still renders the live secondary tiles.
    expect(screen.getByTestId("wallboard-focus-rail")).toBeTruthy();
  });

  it("omits the rail when no secondary tiles are supplied", () => {
    renderSplit({ secondary: undefined });
    expect(screen.queryByTestId("wallboard-focus-rail")).toBeNull();
    expect(screen.getByTestId("wallboard-focus-panel")).toBeTruthy();
  });
});
