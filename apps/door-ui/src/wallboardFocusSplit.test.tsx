// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type {
  AmbientAircraftSummaryPayload,
  AmbientSatelliteOrbitsPayload,
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
  set_at: "2026-07-20T21:22:00Z",
  track: [
    { t_offset_s: 0, azimuth_deg: 315, elevation_deg: 0, lat: 30.1, lng: -128.4 },
    { t_offset_s: 240, azimuth_deg: 45, elevation_deg: 72, lat: 37.6, lng: -122.0 },
    { t_offset_s: 480, azimuth_deg: 135, elevation_deg: 0, lat: 44.9, lng: -114.8 },
  ],
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

// Two satellites over a full period, with absolute sample times, so the panel can draw whole
// loops and a live marker per satellite (ADR-0041).
const ORBIT_EPOCH = Date.parse("2026-07-20T21:00:00Z");
const ORBITS: AmbientSatelliteOrbitsPayload = {
  as_of: "2026-07-20T21:00:00Z",
  satellites: [
    {
      name: "ISS (ZARYA)",
      norad_id: 25544,
      sub_lat: 0,
      sub_lng: -122,
      track: [
        { at: new Date(ORBIT_EPOCH).toISOString(), lat: 0, lng: -122 },
        { at: new Date(ORBIT_EPOCH + 23 * 60000).toISOString(), lat: 45, lng: -80 },
        { at: new Date(ORBIT_EPOCH + 46 * 60000).toISOString(), lat: 0, lng: -40 },
        { at: new Date(ORBIT_EPOCH + 69 * 60000).toISOString(), lat: -45, lng: 0 },
        { at: new Date(ORBIT_EPOCH + 92 * 60000).toISOString(), lat: 0, lng: 40 },
      ],
    },
    {
      name: "HST",
      norad_id: 20580,
      sub_lat: 10,
      sub_lng: -100,
      track: [
        { at: new Date(ORBIT_EPOCH).toISOString(), lat: 10, lng: -100 },
        { at: new Date(ORBIT_EPOCH + 48 * 60000).toISOString(), lat: -10, lng: 80 },
        { at: new Date(ORBIT_EPOCH + 96 * 60000).toISOString(), lat: 10, lng: -100 },
      ],
    },
  ],
};

const EMPTY_AMBIENT = {
  aircraft: null,
  birds: null,
  birdCollageUrl: "",
  satellite: null,
  satelliteOrbits: null,
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

  it("plots the satellite on a globe, with its ground track and the text it always had", () => {
    renderSplit({ channel: "satellite", ambient: { ...EMPTY_AMBIENT, satellite: SATELLITE } });
    const panel = screen.getByTestId("wallboard-focus-panel");
    // The az/el dome answered "which way do I look" (T-325); the owner asked for "where is
    // it", which needs the sub-satellite points the payload now carries (ADR-0030).
    const globe = within(panel).getByTestId("globe");
    expect(globe.getAttribute("aria-label")).toMatch(/ISS/);
    // The text the panel always carried is still there.
    expect(within(panel).getByText("72°")).toBeTruthy();
  });

  it("draws every tracked satellite's whole orbit, a live marker, and a colour legend", () => {
    // The owner asked for all the interesting satellites, not just the single next pass, each
    // with its full loop and where it is now (ADR-0041). Pin the clock to the start of the
    // tracks so the live markers land at the first sample deterministically — both of which
    // sit on the near hemisphere from the door, so both are drawn.
    const clock = vi.spyOn(Date, "now").mockReturnValue(ORBIT_EPOCH);
    try {
      renderSplit({
        channel: "satellite",
        ambient: { ...EMPTY_AMBIENT, satellite: SATELLITE, satelliteOrbits: ORBITS },
      });
      const panel = screen.getByTestId("wallboard-focus-panel");
      // One orbit-path group, and a legend naming each satellite by its short name.
      expect(within(panel).getByTestId("globe-orbits")).toBeTruthy();
      const legend = within(panel).getByTestId("satellite-orbit-legend");
      expect(within(legend).getByText("ISS")).toBeTruthy();
      expect(within(legend).getByText("HST")).toBeTruthy();
      // A live marker per satellite, both near-side at this instant.
      expect(within(panel).getAllByTestId("globe-orbit-sat")).toHaveLength(2);
      // The next-pass text the panel always carried is still present.
      expect(within(panel).getByText("72°")).toBeTruthy();
    } finally {
      clock.mockRestore();
    }
  });

  it("shows the orbits even when the next pass carries no ground track", () => {
    // Orbits are their own event: an older/degraded pass without sub-points must not blank the
    // globe when there are whole orbits to draw.
    const noGround = {
      ...SATELLITE,
      track: SATELLITE.track.map((sample) => ({
        t_offset_s: sample.t_offset_s,
        azimuth_deg: sample.azimuth_deg,
        elevation_deg: sample.elevation_deg,
      })),
    };
    renderSplit({
      channel: "satellite",
      ambient: { ...EMPTY_AMBIENT, satellite: noGround, satelliteOrbits: ORBITS },
    });
    const panel = screen.getByTestId("wallboard-focus-panel");
    expect(within(panel).getByTestId("globe-orbits")).toBeTruthy();
    // The "cannot show where it is" note is about the pass alone — with orbits present it is gone.
    expect(within(panel).queryByText(/cannot show where it is/i)).toBeNull();
  });

  it("says so rather than guessing when a pass carries no ground track", () => {
    // A bearing cannot be turned into a position without the orbit, so an older pass gets
    // an honest empty globe instead of an invented dot.
    const noGround = {
      ...SATELLITE,
      // Strip the ground positions, the way a producer from before ADR-0030 would.
      track: SATELLITE.track.map((sample) => ({
        t_offset_s: sample.t_offset_s,
        azimuth_deg: sample.azimuth_deg,
        elevation_deg: sample.elevation_deg,
      })),
    };
    renderSplit({ channel: "satellite", ambient: { ...EMPTY_AMBIENT, satellite: noGround } });
    const panel = screen.getByTestId("wallboard-focus-panel");
    expect(within(panel).queryByTestId("globe-sat")).toBeNull();
    expect(within(panel).getByText(/cannot show where it is/i)).toBeTruthy();
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

  it("expands the About channel into the panel — its prose plus the numbers", () => {
    // About is now a selectable channel (moved off the doorpad). It has no data source,
    // so it always renders; the focused view carries the facts the ambient tile omits.
    renderSplit({ channel: "about" });
    const panel = screen.getByTestId("wallboard-focus-panel");
    expect(within(panel).getByTestId("about-doorboard")).toBeTruthy();
    expect(within(panel).getByTestId("about-facts")).toBeTruthy();
    expect(within(panel).getByText("If you are not enrolled")).toBeTruthy();
  });
});
