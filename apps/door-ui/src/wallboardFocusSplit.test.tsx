// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type {
  AmbientAircraftSummaryPayload,
  AmbientSatellitePassPayload,
} from "@doorboard/contracts";
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

    // Expanded panel shows the richer aircraft view (incl. the heading column
    // the shrunken ambient tile omits).
    expect(within(panel).getByText("UAL123")).toBeTruthy();
    expect(within(panel).getByText(/Heading 270/)).toBeTruthy();

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
