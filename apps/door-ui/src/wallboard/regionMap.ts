/**
 * The flights map's basemap: baked OSM geometry decoded into Leaflet-shaped polylines.
 *
 * Pure, DOM-free, Leaflet-free — same reason as flightsMapView.ts. The panel's tests mock
 * Leaflet with a chainable no-op, so they would accept any geometry at all; the checks
 * that matter (every baked point lies in the baked box, the box covers what the map can
 * frame, the labels the owner will look for are present) live here where plain node can
 * run them.
 *
 * Why this exists at all: the map used CARTO raster tiles. CARTO now answers keyless
 * requests with valid 200 PNGs stamped "API KEY REQUIRED" diagonally across every tile —
 * `tileerror` never fires, so the old "tiles offline" fallback never engaged and a public
 * hallway screen advertised a licensing error edge to edge. A basemap shipped in the
 * bundle cannot change under the project and needs no network (E-19). The wallboard
 * makes no request to any tile host; a Playwright case pins that.
 */

import type { LatLngTuple } from "./flightsMapView";
import {
  REGION_COASTLINE,
  REGION_COORD_SCALE,
  REGION_PLACES,
  REGION_ROAD_LAYER_ORDER,
  REGION_ROADS,
  REGION_VIEW,
} from "./regionStreets";
import type { RegionPlace, RegionRoadLayer } from "./regionStreets";

export interface LineStyle {
  color: string;
  weight: number;
  opacity: number;
}

/**
 * The doorboard's blue line-work on the dark field, weighted by class so the picture reads
 * as a road network rather than a mesh: motorways brightest and on top, primaries as the
 * faint texture that says "there is a town here".
 */
export const REGION_ROAD_STYLE: Record<RegionRoadLayer, LineStyle> = {
  primary: { color: "#3f7fb8", weight: 1, opacity: 0.55 },
  trunk: { color: "#5aa4e6", weight: 1.6, opacity: 0.8 },
  motorway: { color: "#7cc4ff", weight: 2.2, opacity: 0.92 },
};

/** The shoreline is what makes the dark field read as the peninsula and the Bay. */
export const REGION_COAST_STYLE: LineStyle = { color: "#8fd3ff", weight: 1.3, opacity: 0.85 };

/** Decode one delta-encoded way (lat0, lng0, dlat, dlng, …) into `[lat, lng]` tuples. */
export function decodeWay(way: readonly number[], scale: number): LatLngTuple[] {
  const points: LatLngTuple[] = [];
  let lat = 0;
  let lng = 0;
  for (let index = 0; index + 1 < way.length; index += 2) {
    lat = index === 0 ? way[0]! : lat + way[index]!;
    lng = index === 0 ? way[1]! : lng + way[index + 1]!;
    points.push([lat / scale, lng / scale]);
  }
  return points;
}

export function decodeWays(
  ways: readonly (readonly number[])[],
  scale: number
): LatLngTuple[][] {
  return ways.map((way) => decodeWay(way, scale)).filter((line) => line.length >= 2);
}

/** Every road layer, coarse to fine, decoded and ready for one `L.polyline` each. */
export function regionRoads(): Array<{ layer: RegionRoadLayer; lines: LatLngTuple[][] }> {
  return REGION_ROAD_LAYER_ORDER.map((layer) => ({
    layer,
    lines: decodeWays(REGION_ROADS[layer], REGION_COORD_SCALE),
  }));
}

export function regionCoastline(): LatLngTuple[][] {
  return decodeWays(REGION_COASTLINE, REGION_COORD_SCALE);
}

export function regionPlaces(): readonly RegionPlace[] {
  return REGION_PLACES;
}

export function withinRegion([lat, lng]: LatLngTuple): boolean {
  return (
    lat >= REGION_VIEW.south &&
    lat <= REGION_VIEW.north &&
    lng >= REGION_VIEW.west &&
    lng <= REGION_VIEW.east
  );
}
