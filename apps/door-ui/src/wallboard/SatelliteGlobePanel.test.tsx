// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import type {
  AmbientSatelliteOrbitsPayload,
  AmbientSatellitePassPayload,
} from "@doorboard/contracts";
import { SatelliteGlobePanel, describeSatellite } from "./SatelliteGlobePanel";

afterEach(() => cleanup());

// A next-pass payload with no ground track: it is not the subject here, and orbits render on the
// same globe regardless (ADR-0041).
const PASS: AmbientSatellitePassPayload = {
  satellite: "ISS (ZARYA)",
  rise_at: "2026-07-20T21:14:00Z",
  set_at: "2026-07-20T21:20:00Z",
  max_elevation_deg: 72,
  direction: "NW",
  visible: true,
  track: [],
};

const EPOCH = Date.parse("2026-07-20T21:00:00Z");
function orbit(name: string, noradId: number) {
  // A few near-side samples from the door, enough for a drawable loop.
  return {
    name,
    norad_id: noradId,
    sub_lat: 20,
    sub_lng: -122,
    track: [
      { at: new Date(EPOCH).toISOString(), lat: 20, lng: -122 },
      { at: new Date(EPOCH + 20 * 60000).toISOString(), lat: 40, lng: -100 },
      { at: new Date(EPOCH + 40 * 60000).toISOString(), lat: 20, lng: -80 },
    ],
  };
}

const ORBITS: AmbientSatelliteOrbitsPayload = {
  as_of: "2026-07-20T21:00:00Z",
  satellites: [
    orbit("ISS (ZARYA)", 25544),
    orbit("CSS (TIANHE)", 48274),
    orbit("HST", 20580),
    orbit("NOAA 15", 25338),
    orbit("STARLINK-1234", 99999), // unknown id → graceful fallback
  ],
};

describe("describeSatellite", () => {
  it("maps the known catalogue ids to a readable one-line description", () => {
    expect(describeSatellite(25544, "ISS (ZARYA)")).toEqual({
      name: "ISS",
      blurb: "International Space Station",
    });
    expect(describeSatellite(48274, "CSS (TIANHE)")).toEqual({
      name: "CSS",
      blurb: "Tiangong — China's space station",
    });
    expect(describeSatellite(20580, "HST")).toEqual({
      name: "HST",
      blurb: "Hubble Space Telescope",
    });
    expect(describeSatellite(25338, "NOAA 15")).toEqual({
      name: "NOAA 15",
      blurb: "Weather satellite (NOAA)",
    });
    expect(describeSatellite(28654, "NOAA 18")).toEqual({
      name: "NOAA 18",
      blurb: "Weather satellite (NOAA)",
    });
  });

  it("falls back to the raw short name with no blurb for an unknown id", () => {
    expect(describeSatellite(99999, "STARLINK-1234 (DEB)")).toEqual({
      name: "STARLINK-1234",
      blurb: null,
    });
  });
});

describe("SatelliteGlobePanel legend", () => {
  it("explains each object with a readable description beside its abbreviation", () => {
    render(<SatelliteGlobePanel payload={PASS} orbits={ORBITS} />);
    const legend = screen.getByTestId("satellite-orbit-legend");

    // Short catalogue tags kept (compact, legible on the globe) …
    expect(within(legend).getByText("ISS")).toBeTruthy();
    expect(within(legend).getByText("HST")).toBeTruthy();
    // … now with a plain-English "what is it" line next to them.
    expect(within(legend).getByText("International Space Station")).toBeTruthy();
    expect(within(legend).getByText("Tiangong — China's space station")).toBeTruthy();
    expect(within(legend).getByText("Hubble Space Telescope")).toBeTruthy();
    expect(within(legend).getAllByText("Weather satellite (NOAA)")).toHaveLength(1);
  });

  it("still lists an unknown satellite by name, without inventing a description", () => {
    render(<SatelliteGlobePanel payload={PASS} orbits={ORBITS} />);
    const legend = screen.getByTestId("satellite-orbit-legend");
    expect(within(legend).getByText("STARLINK-1234")).toBeTruthy();
  });

  it("draws the whole-orbit paths group when orbits are present", () => {
    render(<SatelliteGlobePanel payload={PASS} orbits={ORBITS} />);
    expect(screen.getByTestId("globe-orbits")).toBeTruthy();
  });
});
