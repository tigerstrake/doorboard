/**
 * How far the flights map ever zooms out, and what it frames.
 *
 * Pure geometry, deliberately free of Leaflet: the map itself is a chainable mock in the
 * panel's tests, so it would accept any bounds at all and prove nothing about what the
 * wallboard shows. Keeping the rule here makes it testable in plain node.
 *
 * The framing the owner asked for: campus in the middle, detail over the peninsula,
 * nothing beyond the Golden Gate. Previously the map called `fitBounds` on every plotted
 * plane, so one aircraft over the Central Valley pulled the peninsula down to a smudge
 * surrounded by empty land.
 */

export interface Observer {
  latitude: number;
  longitude: number;
}

export type LatLngTuple = [number, number];
/** `[[south, west], [north, east]]`, matching Leaflet's bounds tuple. */
export type BoundsTuple = [LatLngTuple, LatLngTuple];

/** Golden Gate Bridge, the stated northern edge of interest. */
export const GOLDEN_GATE: LatLngTuple = [37.8199, -122.4783];
const CAMPUS_LAT = 37.4275;
const CAMPUS_LNG = -122.1697;

// Half-spans sized so the bridge lands exactly on the edge with the observer centred.
export const VIEW_HALF_SPAN_LAT = GOLDEN_GATE[0] - CAMPUS_LAT; // ≈ 0.392° ≈ 44 km
export const VIEW_HALF_SPAN_LNG = Math.abs(GOLDEN_GATE[1] - CAMPUS_LNG); // ≈ 0.309°

/** The widest view the map may ever show: observer centred, bridge at the edge. */
export function maxViewBounds(observer: Observer): BoundsTuple {
  return [
    [observer.latitude - VIEW_HALF_SPAN_LAT, observer.longitude - VIEW_HALF_SPAN_LNG],
    [observer.latitude + VIEW_HALF_SPAN_LAT, observer.longitude + VIEW_HALF_SPAN_LNG],
  ];
}

export function withinMaxView(point: LatLngTuple, observer: Observer): boolean {
  const [[south, west], [north, east]] = maxViewBounds(observer);
  const [lat, lng] = point;
  return lat >= south && lat <= north && lng >= west && lng <= east;
}

/**
 * The bounds to show for this traffic.
 *
 * Planes outside the capped box are ignored *for scale only* — they still get a marker.
 * With fewer than two points left in view there is nothing meaningful to frame, so the
 * full box is used rather than zooming to street level on a single aircraft.
 */
export function viewBoundsFor(points: LatLngTuple[], observer: Observer): BoundsTuple {
  const inView = points.filter((point) => withinMaxView(point, observer));
  if (inView.length < 2) return maxViewBounds(observer);
  const lats = inView.map(([lat]) => lat);
  const lngs = inView.map(([, lng]) => lng);
  return [
    [Math.min(...lats), Math.min(...lngs)],
    [Math.max(...lats), Math.max(...lngs)],
  ];
}
