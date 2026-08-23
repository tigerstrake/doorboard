import { describe, expect, it } from "vitest";
import {
  DEFAULT_AIRCRAFT_CATEGORY,
  VIEW_HALF_SPAN_LAT,
  VIEW_HALF_SPAN_LNG,
  VIEW_MAX_ZOOM,
  VIEW_ZOOM_FACTOR,
  aircraftCategory,
  aircraftSilhouetteSvg,
  maxViewBounds,
  viewBoundsFor,
  withinMaxView,
} from "./flightsMapView";
import type { AircraftCategory, LatLngTuple } from "./flightsMapView";

/**
 * The framing rule for the flights map, tested without Leaflet.
 *
 * The component's map is a chainable mock in the panel tests, so it accepts any bounds
 * at all and proves nothing about what the wallboard actually shows. Two complaints these
 * pin: the map used to `fitBounds` on every plotted plane (one aircraft over the Central
 * Valley pulled the peninsula down to a smudge), and the resulting view was zoomed too far
 * out (nearby aircraft rendered as tiny specks).
 */

const CAMPUS = { latitude: 37.4275, longitude: -122.1697 };
const GOLDEN_GATE: LatLngTuple = [37.8199, -122.4783]; // the OLD northern edge, now zoomed past
const SAN_JOSE: LatLngTuple = [37.3382, -121.8863]; // ~27 km SE — inside the legacy box, outside the tightened one
const NEARBY: LatLngTuple = [37.49, -122.1]; // ~9 km NE — corridor-distance traffic, kept in view
const FRESNO: LatLngTuple = [36.7378, -119.7871]; // ~200 km away, the old view-wrecker

describe("flights map framing", () => {
  it("centres the observer", () => {
    const [[south, west], [north, east]] = maxViewBounds(CAMPUS);

    expect((south + north) / 2).toBeCloseTo(CAMPUS.latitude, 6);
    expect((west + east) / 2).toBeCloseTo(CAMPUS.longitude, 6);
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
    const bounds = viewBoundsFor([[CAMPUS.latitude, CAMPUS.longitude], NEARBY], CAMPUS);
    const [[south, west], [north, east]] = bounds;

    expect(north - south).toBeLessThan(VIEW_HALF_SPAN_LAT * 2);
    // NEARBY is north-EAST of campus: it sets the northern and eastern edges.
    expect(south).toBeCloseTo(CAMPUS.latitude, 6);
    expect(north).toBeCloseTo(NEARBY[0], 6);
    expect(west).toBeCloseTo(CAMPUS.longitude, 6);
    expect(east).toBeCloseTo(NEARBY[1], 6);
  });

  it("falls back to the full box when nothing is plotted", () => {
    expect(viewBoundsFor([], CAMPUS)).toEqual(maxViewBounds(CAMPUS));
  });

  it("ignores far traffic while still fitting the near traffic", () => {
    const withFar = viewBoundsFor([[CAMPUS.latitude, CAMPUS.longitude], NEARBY, FRESNO], CAMPUS);
    const withoutFar = viewBoundsFor([[CAMPUS.latitude, CAMPUS.longitude], NEARBY], CAMPUS);

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

describe("flights map zoom (owner asked to zoom in more)", () => {
  it("frames a meaningfully tighter box than the legacy campus→Golden-Gate view", () => {
    // Legacy half-spans were observer→Golden-Gate; the view is now VIEW_ZOOM_FACTOR tighter.
    const legacyHalfSpanLat = GOLDEN_GATE[0] - CAMPUS.latitude;
    const legacyHalfSpanLng = Math.abs(GOLDEN_GATE[1] - CAMPUS.longitude);

    expect(VIEW_ZOOM_FACTOR).toBeGreaterThanOrEqual(2);
    expect(VIEW_ZOOM_FACTOR).toBeLessThanOrEqual(3);
    expect(VIEW_HALF_SPAN_LAT).toBeCloseTo(legacyHalfSpanLat / VIEW_ZOOM_FACTOR, 6);
    expect(VIEW_HALF_SPAN_LNG).toBeCloseTo(legacyHalfSpanLng / VIEW_ZOOM_FACTOR, 6);
    // A clear tightening: the box shrank by ~2.5× on each axis.
    expect(VIEW_HALF_SPAN_LAT).toBeLessThan(legacyHalfSpanLat / 2);
    expect(VIEW_HALF_SPAN_LNG).toBeLessThan(legacyHalfSpanLng / 2);
  });

  it("zoomed past the Golden Gate and other legacy-visible landmarks", () => {
    // Both used to sit inside the campus→Golden-Gate box; the tighter view excludes them.
    expect(withinMaxView(GOLDEN_GATE, CAMPUS)).toBe(false);
    expect(withinMaxView(SAN_JOSE, CAMPUS)).toBe(false);
  });

  it("still keeps corridor-distance traffic in frame (does not crop the planes it shows)", () => {
    // The ~9 km plane stays inside the box, so `fitBounds` frames it rather than dropping it.
    expect(withinMaxView(NEARBY, CAMPUS)).toBe(true);
    const bounds = viewBoundsFor([[CAMPUS.latitude, CAMPUS.longitude], NEARBY], CAMPUS);
    const [[south, west], [north, east]] = bounds;
    expect(NEARBY[0]).toBeGreaterThanOrEqual(south);
    expect(NEARBY[0]).toBeLessThanOrEqual(north);
    expect(NEARBY[1]).toBeGreaterThanOrEqual(west);
    expect(NEARBY[1]).toBeLessThanOrEqual(east);
  });

  it("raises the fit-zoom cap so clustered traffic can fill the view", () => {
    // The old cap of 12 left close planes as specks; the marker effect now fits to this.
    expect(VIEW_MAX_ZOOM).toBeGreaterThan(12);
  });
});

describe("aircraft type → category mapping", () => {
  const expectCategory = (type: string, category: AircraftCategory) =>
    expect(aircraftCategory(type)).toBe(category);

  it("maps four-engine heavies from their ICAO designators", () => {
    expectCategory("A388", "heavy"); // A380
    expectCategory("B744", "heavy"); // 747-400
    expectCategory("A342", "heavy"); // A340
    expectCategory("B748", "heavy"); // 747-8
  });

  it("maps narrowbody twin jets", () => {
    expectCategory("A320", "narrow");
    expectCategory("A321", "narrow");
    expectCategory("A319", "narrow");
    expectCategory("B738", "narrow"); // 737-800
    expectCategory("B38M", "narrow"); // 737 MAX 8
    expectCategory("E195", "narrow");
    expectCategory("A20N", "narrow"); // A320neo
  });

  it("maps widebody twins, and A310 overrides the A31x narrowbody bucket", () => {
    expectCategory("B77W", "wide"); // 777-300ER
    expectCategory("B788", "wide"); // 787-8
    expectCategory("A333", "wide"); // A330-300
    expectCategory("A359", "wide"); // A350-900
    expectCategory("B763", "wide"); // 767-300
    expectCategory("A310", "wide"); // NOT narrow, despite sharing the A31 prefix
  });

  it("maps regional jets and turboprops", () => {
    expectCategory("DH8D", "regional"); // Dash 8 Q400
    expectCategory("AT76", "regional"); // ATR 72-600
    expectCategory("CRJ9", "regional"); // CRJ900
    expectCategory("E75L", "regional"); // E175
    expectCategory("E170", "regional");
  });

  it("maps light / GA props, and C130 overrides the Cessna-single bucket", () => {
    expectCategory("C172", "light"); // Cessna 172
    expectCategory("C152", "light");
    expectCategory("PA28", "light"); // Piper Cherokee family
    expectCategory("SR22", "light"); // Cirrus
    expectCategory("DA40", "light"); // Diamond
    expectCategory("C130", "heavy"); // Hercules is NOT a Cessna single
  });

  it("understands human 'manufacturer model' strings from the enricher", () => {
    expectCategory("Boeing 737-800", "narrow");
    expectCategory("Airbus A320", "narrow");
    expectCategory("Boeing 747-400", "heavy");
    expectCategory("Airbus A380-800", "heavy");
    expectCategory("Cessna 172S", "light");
    expectCategory("Boeing 787-9", "wide");
    expectCategory("Bombardier CRJ900", "regional");
  });

  it("falls back to the generic twin-jet for unknown or missing types", () => {
    expect(DEFAULT_AIRCRAFT_CATEGORY).toBe("narrow");
    expectCategory("ZZZZ", "narrow"); // unrecognised designator
    expectCategory("", "narrow"); // empty
    expect(aircraftCategory(null)).toBe("narrow");
    expect(aircraftCategory(undefined)).toBe("narrow");
  });
});

describe("aircraft silhouette SVG", () => {
  const categories: AircraftCategory[] = ["light", "regional", "narrow", "wide", "heavy"];
  const widthOf = (svg: string): number => Number(svg.match(/width="(\d+)"/)?.[1]);
  const engineCount = (svg: string): number => (svg.match(/<ellipse/g) ?? []).length;

  it("tags each silhouette with its category and renders as an SVG", () => {
    for (const category of categories) {
      const svg = aircraftSilhouetteSvg(category);
      expect(svg.startsWith("<svg")).toBe(true);
      expect(svg).toContain(`data-aircraft-category="${category}"`);
      expect(svg).toContain('fill="currentColor"');
    }
  });

  it("makes the four-engine heavy the biggest and gives it four engine marks", () => {
    const widths = categories.map((c) => widthOf(aircraftSilhouetteSvg(c)));
    // Sizes increase light → regional → narrow → wide → heavy.
    for (let i = 1; i < widths.length; i += 1) {
      expect(widths[i]).toBeGreaterThan(widths[i - 1]);
    }
    // A heavy shows four engines; a narrowbody twin shows two.
    expect(engineCount(aircraftSilhouetteSvg("heavy"))).toBe(4);
    expect(engineCount(aircraftSilhouetteSvg("narrow"))).toBe(2);
  });
});
