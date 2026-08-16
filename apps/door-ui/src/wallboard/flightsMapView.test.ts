import { describe, expect, it } from "vitest";
import {
  VIEW_HALF_SPAN_LAT,
  maxViewBounds,
  viewBoundsFor,
  withinMaxView,
} from "./flightsMapView";
import type { LatLngTuple } from "./flightsMapView";

/**
 * The framing rule for the flights map, tested without Leaflet.
 *
 * The component's map is a chainable mock in the panel tests, so it accepts any bounds
 * at all and proves nothing about what the wallboard actually shows. The complaint these
 * pin: the map used to `fitBounds` on every plotted plane, so one aircraft over the
 * Central Valley pulled the peninsula down to a smudge surrounded by empty land.
 */

const CAMPUS = { latitude: 37.4275, longitude: -122.1697 };
const GOLDEN_GATE: LatLngTuple = [37.8199, -122.4783];
const SAN_JOSE: LatLngTuple = [37.3382, -121.8863];
const FRESNO: LatLngTuple = [36.7378, -119.7871]; // ~200 km away, the old view-wrecker

describe("flights map framing", () => {
  it("centres the observer", () => {
    const [[south, west], [north, east]] = maxViewBounds(CAMPUS);

    expect((south + north) / 2).toBeCloseTo(CAMPUS.latitude, 6);
    expect((west + east) / 2).toBeCloseTo(CAMPUS.longitude, 6);
  });

  it("reaches the Golden Gate Bridge but no further north", () => {
    const [, [north]] = maxViewBounds(CAMPUS);

    expect(withinMaxView(GOLDEN_GATE, CAMPUS)).toBe(true);
    // The bridge is the stated edge of interest, so it sits at the boundary.
    expect(north).toBeCloseTo(GOLDEN_GATE[0], 6);
  });

  it("excludes somewhere far inland", () => {
    expect(withinMaxView(FRESNO, CAMPUS)).toBe(false);
  });

  it("does not let a distant plane widen the view", () => {
    const withFarPlane = viewBoundsFor([[CAMPUS.latitude, CAMPUS.longitude], FRESNO], CAMPUS);

    // Only the observer remains in view, so the capped box is used rather than a
    // 200 km-wide fit — this is the actual bug being fixed.
    expect(withFarPlane).toEqual(maxViewBounds(CAMPUS));
  });

  it("tightens onto nearby traffic", () => {
    const bounds = viewBoundsFor([[CAMPUS.latitude, CAMPUS.longitude], SAN_JOSE], CAMPUS);
    const [[south, west], [north, east]] = bounds;

    expect(north - south).toBeLessThan(VIEW_HALF_SPAN_LAT * 2);
    // San Jose is south-EAST of campus: it sets the southern and eastern edges.
    expect(south).toBeCloseTo(SAN_JOSE[0], 6);
    expect(north).toBeCloseTo(CAMPUS.latitude, 6);
    expect(west).toBeCloseTo(CAMPUS.longitude, 6);
    expect(east).toBeCloseTo(SAN_JOSE[1], 6);
  });

  it("falls back to the full box when nothing is plotted", () => {
    expect(viewBoundsFor([], CAMPUS)).toEqual(maxViewBounds(CAMPUS));
  });

  it("ignores far traffic while still fitting the near traffic", () => {
    const withFar = viewBoundsFor([[CAMPUS.latitude, CAMPUS.longitude], SAN_JOSE, FRESNO], CAMPUS);
    const withoutFar = viewBoundsFor([[CAMPUS.latitude, CAMPUS.longitude], SAN_JOSE], CAMPUS);

    // Fresno contributes nothing to the scale: adding it changes the box not at all.
    expect(withFar).toEqual(withoutFar);
  });

  it("moves with the observer rather than hardcoding the peninsula", () => {
    const elsewhere = { latitude: 51.5074, longitude: -0.1278 };
    const [[south, west], [north, east]] = maxViewBounds(elsewhere);

    expect((south + north) / 2).toBeCloseTo(elsewhere.latitude, 6);
    expect((west + east) / 2).toBeCloseTo(elsewhere.longitude, 6);
  });
});
