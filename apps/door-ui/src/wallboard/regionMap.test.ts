import { describe, expect, it } from "vitest";
import {
  decodeWay,
  regionCoastline,
  regionPlaces,
  regionRoads,
  withinRegion,
} from "./regionMap";
import { REGION_VIEW } from "./regionStreets";
import { maxViewBounds } from "./flightsMapView";

// The door, as the flights payload reports it (and the panel's fallback observer).
const OBSERVER = { latitude: 37.4275, longitude: -122.1697 };

describe("regionMap (the flights basemap, baked from OSM)", () => {
  it("decodes a delta-encoded way back to absolute coordinates", () => {
    // lat0, lng0, then deltas — the same scheme as campusStreets.ts, at 1e5.
    expect(decodeWay([3742750, -12216970, 100, -200, -50, 50], 100000)).toEqual([
      [37.4275, -122.1697],
      [37.4285, -122.1717],
      [37.428, -122.1712],
    ]);
  });

  it("bakes every road class and the shoreline, and every point lies inside the baked box", () => {
    // A point outside the box would mean the clip step regressed; a point far outside would
    // draw a chord across the map. Either is silent on a mocked Leaflet. Lines are trimmed
    // to the box edge exactly, so only integer rounding (half a unit at 1e5) is allowed.
    const slack = 1e-5;
    const inBox = ([lat, lng]: [number, number]) =>
      lat >= REGION_VIEW.south - slack &&
      lat <= REGION_VIEW.north + slack &&
      lng >= REGION_VIEW.west - slack &&
      lng <= REGION_VIEW.east + slack;
    for (const { layer, lines } of regionRoads()) {
      expect(lines.length, layer).toBeGreaterThan(0);
      for (const line of lines) {
        expect(line.length).toBeGreaterThanOrEqual(2);
        for (const point of line) expect(inBox(point), `${layer} ${point}`).toBe(true);
      }
    }
    const coast = regionCoastline();
    expect(coast.length).toBeGreaterThan(0);
    for (const line of coast) for (const point of line) expect(inBox(point), `${point}`).toBe(true);
  });

  it("covers everything the map can frame around the door", () => {
    // The map never zooms wider than maxViewBounds (~35 km tall), but at 1920x1080 the
    // panel is about twice as wide as tall, so fitting that box shows ~±0.45° of longitude.
    // The bake must reach that far or roads stop at an invisible edge inside the frame.
    const [[south, west], [north, east]] = maxViewBounds(OBSERVER);
    expect(REGION_VIEW.south).toBeLessThanOrEqual(south);
    expect(REGION_VIEW.north).toBeGreaterThanOrEqual(north);
    expect(REGION_VIEW.west).toBeLessThanOrEqual(west);
    expect(REGION_VIEW.east).toBeGreaterThanOrEqual(east);
    expect(OBSERVER.longitude - REGION_VIEW.west).toBeGreaterThanOrEqual(0.45);
    expect(REGION_VIEW.east - OBSERVER.longitude).toBeGreaterThanOrEqual(0.45);
  });

  it("labels the airports by IATA code and the towns by name, all inside the box", () => {
    const places = regionPlaces();
    const byName = new Map(places.map((place) => [place.name, place]));
    // On a map of aircraft the one label people look for is the airport a plane is
    // lining up for; the two big ones and the one next door must be there.
    for (const code of ["SFO", "SJC", "PAO"]) {
      expect(byName.get(code)?.kind, code).toBe("airport");
    }
    expect(byName.get("Palo Alto")?.kind).toBe("city");
    expect(byName.get("Menlo Park")?.kind).toBe("town");
    for (const place of places) {
      expect(withinRegion([place.lat, place.lng]), place.name).toBe(true);
      expect(place.name.length).toBeGreaterThan(0);
    }
  });
});
