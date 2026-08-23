/**
 * How far the flights map ever zooms out, what it frames, and which silhouette
 * each aircraft gets.
 *
 * Pure geometry + pure lookups, deliberately free of Leaflet and the DOM: the map itself
 * is a chainable mock in the panel's tests, so it would accept any bounds at all and prove
 * nothing about what the wallboard shows. Keeping the rules here makes them testable in
 * plain node.
 *
 * Framing: campus in the middle, the observer's *local* area filling the view. The owner
 * flew the tile and found it zoomed too far out — nearby aircraft rendered as tiny specks
 * over a wide region. The view is now tightened ~2.5× (see `VIEW_ZOOM_FACTOR`): planes at
 * corridor distance read clearly, while a plane over the Central Valley still gets a marker
 * without dragging the peninsula down to a smudge.
 */

export interface Observer {
  latitude: number;
  longitude: number;
}

export type LatLngTuple = [number, number];
/** `[[south, west], [north, east]]`, matching Leaflet's bounds tuple. */
export type BoundsTuple = [LatLngTuple, LatLngTuple];

/** Golden Gate Bridge — the *old* northern edge, kept as the reference the view zooms in from. */
export const GOLDEN_GATE: LatLngTuple = [37.8199, -122.4783];
const CAMPUS_LAT = 37.4275;
const CAMPUS_LNG = -122.1697;

// The pre-complaint framing: observer centred, Golden Gate on the edge (~44 km half-span).
const LEGACY_HALF_SPAN_LAT = GOLDEN_GATE[0] - CAMPUS_LAT; // ≈ 0.392° ≈ 44 km
const LEGACY_HALF_SPAN_LNG = Math.abs(GOLDEN_GATE[1] - CAMPUS_LNG); // ≈ 0.309°

/**
 * How much tighter than the legacy campus→Golden-Gate box the map now frames.
 * The owner asked to "zoom in more" (roughly 2–3×); 2.5 lands the local area — ~35 km
 * tall × ~22 km wide — squarely in the frame instead of a wide peninsula of empty land.
 */
export const VIEW_ZOOM_FACTOR = 2.5;

// Half-spans sized so the observer's neighbourhood fills the widest view.
export const VIEW_HALF_SPAN_LAT = LEGACY_HALF_SPAN_LAT / VIEW_ZOOM_FACTOR; // ≈ 0.157° ≈ 17 km
export const VIEW_HALF_SPAN_LNG = LEGACY_HALF_SPAN_LNG / VIEW_ZOOM_FACTOR; // ≈ 0.124°

/**
 * How far Leaflet may zoom in when fitting tight traffic. Bumped from 12: at 12 a cluster
 * of planes a couple of km apart still showed as specks because `fitBounds` refused to
 * zoom past that level. 14 lets clustered traffic actually fill the view; the marker effect
 * passes this as `fitBounds({ maxZoom })`.
 */
export const VIEW_MAX_ZOOM = 14;

/** The widest view the map may ever show: observer centred, local area filling the frame. */
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

// ---------------------------------------------------------------------------
// Aircraft silhouettes — FlightRadar24-style top-view icons chosen by category.
//
// The feed's `aircraft_type` may arrive either as an ICAO type designator (e.g. "A320",
// "B738", "A388", "C172" — how adsb.fi reports it, ADR-0032) or as a human string (e.g.
// "Boeing 737-800" — how the adsbdb enricher reports it, ADR-0015). Both are mapped here.
// ---------------------------------------------------------------------------

export type AircraftCategory = "light" | "regional" | "narrow" | "wide" | "heavy";

/** Unknown / missing type falls back to the generic twin-jet, a sensible default. */
export const DEFAULT_AIRCRAFT_CATEGORY: AircraftCategory = "narrow";

/**
 * ICAO type-designator prefixes → category. Resolved longest-prefix-first (4→3→2 chars),
 * so a specific entry (e.g. `A310` widebody) overrides a broader one (`A31` narrowbody)
 * and real designators land in the right bucket while unknown ones degrade gracefully.
 */
const TYPE_PREFIX_CATEGORY: Record<string, AircraftCategory> = {
  // --- Four-engine heavies (biggest silhouette, 4 engine marks) ---
  A34: "heavy", // A340
  A38: "heavy", // A380 / A388
  B74: "heavy", // B747 / B744 / B748
  A124: "heavy", // An-124
  IL96: "heavy", // Il-96
  DC8: "heavy", // DC-8 (four-engine)
  C130: "heavy", // C-130 Hercules — overrides the C1 (light) bucket

  // --- Widebody twins (larger, longer twin-jet) ---
  A30: "wide", // A300
  A310: "wide", // A310 — overrides A31 (narrow)
  A33: "wide", // A330
  A35: "wide", // A350
  B76: "wide", // B767
  B77: "wide", // B777
  B78: "wide", // B787
  MD11: "wide", // MD-11 (widebody trijet)
  DC10: "wide", // DC-10
  L101: "wide", // L-1011 TriStar

  // --- Narrowbody twin jets (the standard twin-jet, also the default) ---
  A31: "narrow", // A318 / A319 (A310 handled above)
  A32: "narrow", // A320 / A321
  A19N: "narrow", // A319neo
  A20N: "narrow", // A320neo
  A21N: "narrow", // A321neo
  B73: "narrow", // B737 classic / NG
  B37M: "narrow", // 737 MAX 7
  B38M: "narrow", // 737 MAX 8
  B39M: "narrow", // 737 MAX 9
  B3XM: "narrow", // 737 MAX 10
  B75: "narrow", // B757
  B71: "narrow", // B717
  E19: "narrow", // E190 / E195
  E29: "narrow", // E190-E2 / E195-E2
  BCS: "narrow", // A220 (BCS1 / BCS3)
  MD8: "narrow", // MD-80 family
  MD9: "narrow", // MD-90
  DC9: "narrow", // DC-9
  F100: "narrow", // Fokker 100

  // --- Regional jets & turboprops ---
  DH8: "regional", // Dash 8 / Q400
  DHC: "regional", // DHC-6 Twin Otter & kin
  AT: "regional", // ATR 42 / 72 (AT43..AT76), BAe ATP
  E17: "regional", // E170 / E175
  E75: "regional", // E175 (E75L / E75S)
  E13: "regional", // ERJ-135
  E14: "regional", // ERJ-145
  E45: "regional", // ERJ-145 (E45X)
  CRJ: "regional", // CRJ 100..1000
  SF3: "regional", // Saab 340
  SB20: "regional", // Saab 2000
  JS: "regional", // BAe Jetstream
  D328: "regional", // Dornier 328
  D228: "regional", // Dornier 228
  B190: "regional", // Beech 1900
  B350: "regional", // King Air 350
  BE20: "regional", // King Air 200
  BE30: "regional", // King Air 300/350 — overrides BE3 (light)
  BE9: "regional", // King Air 90
  F50: "regional", // Fokker 50

  // --- Light / GA props ---
  C1: "light", // Cessna 150/152/162/172/177/182/185 (C130 overridden above)
  C20: "light", // Cessna 206/207/208 Caravan
  C21: "light", // Cessna 210
  P28: "light", // Piper PA-28 (P28A/P28R/…)
  P32: "light", // Piper PA-32 (P32R/P32T)
  PA2: "light", // Piper PA-23/24/25/27
  PA3: "light", // Piper PA-31/32/34
  PA4: "light", // Piper PA-44/46
  SR2: "light", // Cirrus SR20 / SR22
  DA: "light", // Diamond DA20/40/42/62
  DV: "light", // Diamond DV20 Katana
  TBM: "light", // Daher/Socata TBM single turboprop
  PC12: "light", // Pilatus PC-12
  M20: "light", // Mooney M20
  BE3: "light", // Beech Bonanza (BE33/35/36)
  BE5: "light", // Beech Baron (BE55/58)
  BE76: "light", // Beech Duchess
};

function categoryFromDesignator(designator: string): AircraftCategory | null {
  const s = designator.toUpperCase().replace(/[^A-Z0-9]/g, "");
  // Longest prefix wins so specific overrides (A310, C130, BE30…) beat their broader bucket.
  for (const len of [4, 3, 2]) {
    if (s.length >= len) {
      const hit = TYPE_PREFIX_CATEGORY[s.slice(0, len)];
      if (hit) return hit;
    }
  }
  return null;
}

// Human-readable "manufacturer + model" strings (the adsbdb enricher's format) mapped to a
// synthetic designator we can then run through the prefix table.
const NAME_MANUFACTURER: Array<[RegExp, string]> = [
  [/BOEING/, "B"],
  [/AIRBUS/, "A"],
  [/EMBRAER/, "E"],
  [/CESSNA/, "C"],
  [/CIRRUS/, "SR"],
  [/PIPER/, "PA"],
  [/DIAMOND/, "DA"],
  [/MOONEY/, "M"],
  [/PILATUS/, "PC"],
  [/ATR/, "AT"],
  [/CANADAIR|BOMBARDIER|CRJ/, "CRJ"],
  [/DE ?HAVILLAND|DASH ?8|DHC/, "DH8"],
  [/MC ?DONNELL|DOUGLAS|MD-?\d/, "MD"],
  [/BEECH/, "B"],
  [/SAAB/, "SF"],
];

function designatorFromName(name: string): string | null {
  const upper = name.toUpperCase();
  const digits = upper.match(/(\d{2,4})/);
  if (!digits) return null;
  for (const [pattern, prefix] of NAME_MANUFACTURER) {
    if (pattern.test(upper)) return prefix + digits[1];
  }
  return null;
}

/**
 * Map an aircraft type to a silhouette category. Accepts an ICAO designator (`A388`,
 * `B738`, `C172`) or a human string (`"Boeing 737-800"`); anything unrecognised — or a
 * missing type — falls back to the generic twin-jet.
 */
export function aircraftCategory(aircraftType: string | null | undefined): AircraftCategory {
  const raw = aircraftType?.trim();
  if (!raw) return DEFAULT_AIRCRAFT_CATEGORY;
  const direct = categoryFromDesignator(raw);
  if (direct) return direct;
  const synthetic = designatorFromName(raw);
  if (synthetic) {
    const fromName = categoryFromDesignator(synthetic);
    if (fromName) return fromName;
  }
  return DEFAULT_AIRCRAFT_CATEGORY;
}

// Pixel size of the silhouette, scaling subtly with category (heavy ≈ 1.9× light) so a
// 747 reads as visibly bigger than a Cessna even before you notice the engine count.
const SILHOUETTE_SIZE: Record<AircraftCategory, number> = {
  light: 22,
  regional: 26,
  narrow: 30,
  wide: 36,
  heavy: 42,
};

// Top-view silhouettes, nose pointing up (north) inside a 40×40 box so the panel can rotate
// the whole SVG by `heading` and have it point the right way. Filled with `currentColor`
// so the wallboard's accent colour (and the light/dark themes) drive them. Kept simple:
// size, wing sweep, and engine/prop count carry the category at marker scale.
const SILHOUETTE_BODY: Record<AircraftCategory, string> = {
  // Single-engine prop: small, straight high wings, one nose prop disc.
  light:
    '<path d="M20 8 C18.6 8 18 10 18 12.5 L18 26 C18 29 18.7 31 20 31 C21.3 31 22 29 22 26 L22 12.5 C22 10 21.4 8 20 8 Z"/>' +
    '<path d="M18.3 16 L5 18 L5 20 L18.3 19 Z"/><path d="M21.7 16 L35 18 L35 20 L21.7 19 Z"/>' +
    '<path d="M19 26 L14 29 L14 30 L19 28 Z"/><path d="M21 26 L26 29 L26 30 L21 28 Z"/>' +
    '<rect x="13" y="6.4" width="14" height="1.6" rx="0.8"/><circle cx="20" cy="8" r="1.1"/>',
  // Twin turboprop: mid size, straighter wings, two wing-mounted props.
  regional:
    '<path d="M20 5 C18.2 5 17.3 7.5 17.3 11 L17.3 27 C17.3 31 18.2 33.5 20 33.5 C21.8 33.5 22.7 31 22.7 27 L22.7 11 C22.7 7.5 21.8 5 20 5 Z"/>' +
    '<path d="M18 17 L4.5 21 L4.5 23 L18 21.5 Z"/><path d="M22 17 L35.5 21 L35.5 23 L22 21.5 Z"/>' +
    '<path d="M19 28.5 L13 31.5 L13 32.5 L19 30.5 Z"/><path d="M21 28.5 L27 31.5 L27 32.5 L21 30.5 Z"/>' +
    '<rect x="9.7" y="16" width="2.6" height="6" rx="1.3"/><rect x="27.7" y="16" width="2.6" height="6" rx="1.3"/>' +
    '<rect x="7.5" y="14.7" width="7" height="1.4" rx="0.7"/><rect x="25.5" y="14.7" width="7" height="1.4" rx="0.7"/>',
  // Standard narrowbody twin jet: swept wings, two under-wing engines.
  narrow:
    '<path d="M20 4 C18 4 17 7 17 11 L17 28 C17 33 18 36 20 36 C22 36 23 33 23 28 L23 11 C23 7 22 4 20 4 Z"/>' +
    '<path d="M18 19 L4 27 L4 29 L18 24 Z"/><path d="M22 19 L36 27 L36 29 L22 24 Z"/>' +
    '<path d="M19 31 L12 34 L12 35 L19 33 Z"/><path d="M21 31 L28 34 L28 35 L21 33 Z"/>' +
    '<ellipse cx="11" cy="25" rx="1.5" ry="2.3"/><ellipse cx="29" cy="25" rx="1.5" ry="2.3"/>',
  // Widebody twin: longer fuselage, larger wings and engines than the narrowbody.
  wide:
    '<path d="M20 3 C17.5 3 16.5 6.5 16.5 11 L16.5 30 C16.5 35 17.5 38 20 38 C22.5 38 23.5 35 23.5 30 L23.5 11 C23.5 6.5 22.5 3 20 3 Z"/>' +
    '<path d="M17.5 18 L2 27 L2 29.5 L17.5 24 Z"/><path d="M22.5 18 L38 27 L38 29.5 L22.5 24 Z"/>' +
    '<path d="M18.5 32 L10 36 L10 37 L18.5 34 Z"/><path d="M21.5 32 L30 36 L30 37 L21.5 34 Z"/>' +
    '<ellipse cx="10.5" cy="25" rx="2" ry="3"/><ellipse cx="29.5" cy="25" rx="2" ry="3"/>',
  // Four-engine heavy: biggest silhouette, four engine marks (two per wing).
  heavy:
    '<path d="M20 2.5 C17 2.5 16 6 16 11 L16 31 C16 36.5 17.5 39.5 20 39.5 C22.5 39.5 24 36.5 24 31 L24 11 C24 6 23 2.5 20 2.5 Z"/>' +
    '<path d="M17 17 L1.5 27 L1.5 29.5 L17 23.5 Z"/><path d="M23 17 L38.5 27 L38.5 29.5 L23 23.5 Z"/>' +
    '<path d="M18 33 L9 37.5 L9 38.5 L18 35.5 Z"/><path d="M22 33 L31 37.5 L31 38.5 L22 35.5 Z"/>' +
    '<ellipse cx="12" cy="21.5" rx="1.5" ry="2.4"/><ellipse cx="7" cy="24.5" rx="1.5" ry="2.4"/>' +
    '<ellipse cx="28" cy="21.5" rx="1.5" ry="2.4"/><ellipse cx="33" cy="24.5" rx="1.5" ry="2.4"/>',
};

/**
 * Inline SVG silhouette for a category, sized by category and filled with `currentColor`.
 * The `data-aircraft-category` attribute lets tests (and CSS) target the chosen icon.
 */
export function aircraftSilhouetteSvg(category: AircraftCategory): string {
  const size = SILHOUETTE_SIZE[category];
  return (
    `<svg class="flight-silhouette" data-aircraft-category="${category}" ` +
    `width="${size}" height="${size}" viewBox="0 0 40 40" fill="currentColor" ` +
    `aria-hidden="true" focusable="false">${SILHOUETTE_BODY[category]}</svg>`
  );
}
