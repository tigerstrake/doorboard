/**
 * A stylised campus map for the dining recommendation: where the recommended hall is.
 *
 * Geometry only, no DOM — same reason as `skyDome.ts`. A projection error here puts a
 * confident dot on the wrong side of campus, which reads as fact and cannot be caught by
 * rendering the component with a mocked map.
 *
 * Every coordinate below is a real one, geocoded from OpenStreetMap rather than sketched.
 * "Stylised" describes how it is *drawn* — the doorboard's blue outlines on a dark field,
 * a handful of landmarks instead of every building — not how it is positioned. A pretty
 * diagram that puts Wilbur where Ricker is would be worse than no map.
 */

export interface LatLng {
  lat: number;
  lng: number;
}

export interface Point {
  x: number;
  y: number;
}

/**
 * The framed area: central and residential campus, which is where the dining halls are.
 *
 * Deliberately excludes the stadium and the medical centre. Including them would stretch
 * the view until the halls — the only thing this map exists to distinguish between —
 * clustered into a few pixels of each other.
 */
export const CAMPUS_VIEW = {
  south: 37.4196,
  north: 37.4288,
  west: -122.1818,
  east: -122.1618,
} as const;

/** Longitude degrees are shorter than latitude ones; at 37.4°N by this much. */
const LNG_SCALE = Math.cos((37.424 * Math.PI) / 180); // ≈ 0.794

const VIEW_LAT_SPAN = CAMPUS_VIEW.north - CAMPUS_VIEW.south;
const VIEW_LNG_SPAN = (CAMPUS_VIEW.east - CAMPUS_VIEW.west) * LNG_SCALE;

/** Width/height of the drawn area, so callers can size a viewBox without distortion. */
export const CAMPUS_ASPECT = VIEW_LNG_SPAN / VIEW_LAT_SPAN;

/**
 * Project a coordinate into a `width × height` box, north up.
 *
 * Equirectangular with a fixed longitude scale. Over 2 km of campus the error against a
 * proper projection is well under a metre — far below the precision of "this building".
 */
export function projectToCampus(at: LatLng, width: number, height: number): Point {
  const fx = ((at.lng - CAMPUS_VIEW.west) * LNG_SCALE) / VIEW_LNG_SPAN;
  const fy = (CAMPUS_VIEW.north - at.lat) / VIEW_LAT_SPAN; // screen y grows southward
  return { x: fx * width, y: fy * height };
}

export function withinCampusView(at: LatLng): boolean {
  return (
    at.lat >= CAMPUS_VIEW.south &&
    at.lat <= CAMPUS_VIEW.north &&
    at.lng >= CAMPUS_VIEW.west &&
    at.lng <= CAMPUS_VIEW.east
  );
}

export interface DiningHall {
  /** Canonical display name. */
  name: string;
  at: LatLng;
  /**
   * Lowercase substrings that identify this hall in a provider's own wording. The menu site
   * names halls itself and its wording changes, so matching is by keyword rather than by
   * an exact string we do not control.
   */
  aliases: readonly string[];
  /** True when the point is the residence building, the dining room being inside it. */
  approximate?: boolean;
}

/**
 * Geocoded from OpenStreetMap (August 2026). Wilbur and Stern agree with Stanford's own
 * campus map to about 20 m, which is the accuracy this map needs and better than it shows.
 *
 * A hall that is not in here gets no point — see `resolveDiningHall`. That is the whole
 * reason this is a catalogue and not a formula: an unknown name must fail visibly.
 */
export const DINING_HALLS: readonly DiningHall[] = [
  { name: "Wilbur", at: { lat: 37.42396, lng: -122.16316 }, aliases: ["wilbur"] },
  { name: "Stern", at: { lat: 37.42432, lng: -122.16563 }, aliases: ["stern"] },
  {
    name: "Arrillaga Family Dining Commons",
    at: { lat: 37.42542, lng: -122.16409 },
    aliases: ["arrillaga", "afdc"],
  },
  { name: "Ricker", at: { lat: 37.42552, lng: -122.18048 }, aliases: ["ricker"] },
  { name: "Lakeside", at: { lat: 37.42473, lng: -122.17641 }, aliases: ["lakeside"] },
  {
    name: "Florence Moore",
    at: { lat: 37.422, lng: -122.17213 },
    aliases: ["florence moore", "flomo", "florence"],
    approximate: true,
  },
  {
    name: "Branner",
    at: { lat: 37.42553, lng: -122.16282 },
    aliases: ["branner"],
    approximate: true,
  },
  {
    name: "Gerhard Casper",
    at: { lat: 37.4243, lng: -122.17825 },
    aliases: ["casper", "gerhard", "governor"],
    approximate: true,
  },
];

/**
 * Find the hall a provider means, or null.
 *
 * Null is a real answer: the caller draws the campus with no point and says the location is
 * unknown. Guessing the nearest name would put a confident dot on the wrong building.
 */
export function resolveDiningHall(hallName: string | null | undefined): DiningHall | null {
  if (!hallName) return null;
  const needle = hallName.toLowerCase();
  // Longest alias first, so "florence moore" cannot lose to a shorter accidental match.
  const byLength = DINING_HALLS.flatMap((hall) =>
    hall.aliases.map((alias) => ({ hall, alias }))
  ).sort((a, b) => b.alias.length - a.alias.length);
  return byLength.find(({ alias }) => needle.includes(alias))?.hall ?? null;
}

/**
 * The hall named in a `title` of the shape "{hall} — {meal}".
 *
 * A fallback for events produced before `hall` existed as its own field (ADR-0026), which
 * includes anything still in yesterday's cache. New producers should not rely on it.
 */
export function hallFromTitle(title: string | null | undefined): string | null {
  if (!title) return null;
  const [head] = title.split(/\s+[—–-]\s+/);
  const trimmed = head?.trim();
  return trimmed ? trimmed : null;
}

export interface CampusLandmark {
  label: string;
  /** Where to put the label relative to the shape, so it does not sit on a marker. */
  labelAt?: "inside" | "below";
  /** An ellipse for water and open ground, a rect for a building block. */
  shape: "ellipse" | "rect";
  /** Real bounding box, south/north/west/east. */
  bounds: { south: number; north: number; west: number; east: number };
}

/**
 * Enough landmarks to read as Stanford, all from real OSM extents.
 *
 * The lake's outline is its actual bounding box; the Quad's is anchored on Memorial Church's
 * real position and sized by eye, which is why it is drawn as a plain block and labelled
 * rather than traced.
 */
export const CAMPUS_LANDMARKS: readonly CampusLandmark[] = [
  {
    label: "Lake Lagunita",
    shape: "ellipse",
    labelAt: "inside",
    bounds: { south: 37.42044, north: 37.42444, west: -122.17854, east: -122.17389 },
  },
  {
    // The doorboard's own marker sits in here, so the label goes underneath it.
    label: "Main Quad",
    shape: "rect",
    labelAt: "below",
    bounds: { south: 37.4264, north: 37.4278, west: -122.1712, east: -122.1688 },
  },
  {
    label: "White Plaza",
    shape: "rect",
    labelAt: "inside",
    bounds: { south: 37.42425, north: 37.42558, west: -122.1704, east: -122.16904 },
  },
];

/** Hoover Tower, the one landmark everyone orients from. Real OSM position. */
export const HOOVER_TOWER: LatLng = { lat: 37.42762, lng: -122.16699 };

/** Where the doorboard is — the same observer the flights map centres on. */
export const DOORBOARD_AT: LatLng = { lat: 37.4275, lng: -122.1697 };
