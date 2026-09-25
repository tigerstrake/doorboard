// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import type { AmbientAircraftSummaryPayload } from "@doorboard/contracts";

// Leaflet needs a real DOM/canvas, which does not exist under jsdom. Mock it with a
// chainable no-op API so the panel mounts (and its map lifecycle runs) without the
// real library. Hoisted so the tests can assert WHICH layers the panel builds.
const leaflet = vi.hoisted(() => {
  const chainable = (extra: Record<string, unknown> = {}) => {
    const api: Record<string, unknown> = { ...extra };
    for (const method of [
      "setView",
      "addTo",
      "on",
      "clearLayers",
      "remove",
      "invalidateSize",
      "fitBounds",
    ]) {
      api[method] = () => api;
    }
    return api;
  };
  const attributionControl = { setPrefix: vi.fn(), addAttribution: vi.fn() };
  return {
    attributionControl,
    map: vi.fn(() => chainable({ attributionControl })),
    tileLayer: vi.fn(() => chainable()),
    polyline: vi.fn(() => chainable()),
    layerGroup: vi.fn(() => chainable()),
    marker: vi.fn(() => chainable()),
    divIcon: vi.fn(() => ({})),
    latLngBounds: vi.fn(() => ({})),
  };
});
vi.mock("leaflet", () => ({ default: leaflet, ...leaflet }));

// Also mock the CSS side-effect import so vitest doesn't try to parse it.
vi.mock("leaflet/dist/leaflet.css", () => ({}));

import { AircraftFocusPanel } from "./AircraftFocusPanel";

afterEach(cleanup);

const enrichedPlane = {
  callsign: "SWA456",
  altitude_ft: 12000,
  distance_km: 8.5,
  heading: 270,
  latitude: 37.46,
  longitude: -122.11,
  ground_speed_kmh: 742,
  vertical_rate_fpm: 1280,
  registration: "N8329B",
  aircraft_type: "Boeing 737-800",
  operator: "Southwest Airlines",
  origin: "LAX",
  destination: "SFO",
  photo_url: "https://example.test/737.jpg",
  photo_attribution: "Photo: Tester",
};

const sparsePlane = {
  callsign: "N512QS",
  altitude_ft: 4200,
  distance_km: 3.1,
  heading: 30,
};

function makePayload(nearby: AmbientAircraftSummaryPayload["nearby"]): AmbientAircraftSummaryPayload {
  return {
    nearby,
    as_of: "2026-07-04T12:34:56.123Z",
    observer: { latitude: 37.4275, longitude: -122.1697 },
  };
}

describe("AircraftFocusPanel (Flights focus)", () => {
  it("renders the live map container and a card per plane", () => {
    render(<AircraftFocusPanel payload={makePayload([enrichedPlane, sparsePlane])} />);
    expect(screen.getByTestId("flights-map")).toBeTruthy();
    expect(screen.getAllByTestId("flight-card").length).toBe(2);
  });

  it("shows every enriched field when present", () => {
    render(<AircraftFocusPanel payload={makePayload([enrichedPlane])} />);
    expect(screen.getByText("SWA456")).toBeTruthy();
    expect(screen.getByText("N8329B")).toBeTruthy();
    expect(screen.getByText("Southwest Airlines")).toBeTruthy();
    expect(screen.getByText("Boeing 737-800")).toBeTruthy();
    expect(screen.getByText(/LAX/)).toBeTruthy();
    expect(screen.getByText(/SFO/)).toBeTruthy();
    expect(screen.getByText("742 km/h")).toBeTruthy();
    expect(screen.getByText("12,000 ft")).toBeTruthy();
    expect(screen.getByText("8.5 km")).toBeTruthy();
    expect(screen.getByText("270°")).toBeTruthy();
    // Positive vertical rate => climbing trend.
    expect(screen.getByText(/climbing/)).toBeTruthy();
    // Photo thumbnail rendered from photo_url with its attribution.
    const img = screen.getByAltText(/SWA456 aircraft/) as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("https://example.test/737.jpg");
    expect(screen.getByText("Photo: Tester")).toBeTruthy();
  });

  it("tolerates a sparse plane: only present fields render, no crash, no photo", () => {
    render(<AircraftFocusPanel payload={makePayload([sparsePlane])} />);
    expect(screen.getByText("N512QS")).toBeTruthy();
    expect(screen.getByText("4,200 ft")).toBeTruthy();
    // Absent enriched fields must not appear and must not throw.
    expect(screen.queryByText("Southwest Airlines")).toBeNull();
    expect(screen.queryByText(/→/)).toBeNull();
    // No photo thumbnail for a plane without photo_url (queryByAltText only
    // matches <img> alt text, not the map's aria-label).
    expect(screen.queryByAltText(/aircraft/)).toBeNull();
  });

  it("shows an empty note when there are no nearby aircraft", () => {
    render(<AircraftFocusPanel payload={makePayload([])} />);
    expect(screen.getByText(/No nearby aircraft/)).toBeTruthy();
    // Map still mounts so the surface never goes blank.
    expect(screen.getByTestId("flights-map")).toBeTruthy();
  });

  it("draws a baked vector basemap and never adds a tile layer", () => {
    // CARTO began serving keyless tiles stamped "API KEY REQUIRED" as valid 200 PNGs, so
    // a raster layer cannot even detect the problem. The basemap is baked OSM geometry
    // (roads, shoreline, place labels) and the map requests nothing from a tile host.
    leaflet.tileLayer.mockClear();
    leaflet.polyline.mockClear();
    leaflet.attributionControl.addAttribution.mockClear();
    render(<AircraftFocusPanel payload={makePayload([sparsePlane])} />);
    expect(leaflet.tileLayer).not.toHaveBeenCalled();
    // Three road classes + the shoreline.
    expect(leaflet.polyline).toHaveBeenCalledTimes(4);
    // ODbL: the credit is rendered wherever the data is shown.
    expect(leaflet.attributionControl.addAttribution).toHaveBeenCalledWith(
      "© OpenStreetMap contributors",
    );
    expect(screen.queryByText(/tiles offline/i)).toBeNull();
  });

  it("falls back to the default observer when the payload omits one", () => {
    const payload: AmbientAircraftSummaryPayload = {
      nearby: [enrichedPlane],
      as_of: "2026-07-04T12:34:56.123Z",
    };
    // Should not throw even though `observer` is undefined.
    render(<AircraftFocusPanel payload={payload} />);
    expect(screen.getByTestId("flights-map")).toBeTruthy();
  });
});
