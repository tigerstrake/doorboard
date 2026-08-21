/**
 * Orthographic globe projection, for the satellite panel.
 *
 * Geometry only, no DOM — the same reason campusMap.ts is separate. The
 * failure mode here is specific and silent: in an orthographic projection half the sphere is
 * behind the globe, and a point on the far side still projects to a perfectly plausible
 * position on the near face. Forget to cull it and Asia draws over the Pacific, the satellite
 * appears on the wrong side of the world, and nothing looks broken.
 */

export interface LatLng {
  lat: number;
  lng: number;
}

export interface Point {
  x: number;
  y: number;
}

export interface GlobePoint extends Point {
  /** False when the point is on the far hemisphere and must not be drawn. */
  visible: boolean;
  /** Cosine of the angle from the view centre: 1 at the centre, 0 at the limb. */
  facing: number;
}

const RAD = Math.PI / 180;

/**
 * Project onto a unit disc centred on `centre`, north up.
 *
 * Returns `visible: false` for the far hemisphere rather than omitting the point, so callers
 * can decide between skipping it and breaking a polyline at the limb — a coastline that
 * crosses the edge needs the break, or it gets a chord straight across the globe.
 */
export function projectToGlobe(at: LatLng, centre: LatLng): GlobePoint {
  const lat = at.lat * RAD;
  const lng = at.lng * RAD;
  const lat0 = centre.lat * RAD;
  const lng0 = centre.lng * RAD;
  const dLng = lng - lng0;

  const cosFacing =
    Math.sin(lat0) * Math.sin(lat) + Math.cos(lat0) * Math.cos(lat) * Math.cos(dLng);
  return {
    x: Math.cos(lat) * Math.sin(dLng),
    // Screen y grows downward, so this is negated to put north up.
    y: -(Math.cos(lat0) * Math.sin(lat) - Math.sin(lat0) * Math.cos(lat) * Math.cos(dLng)),
    visible: cosFacing >= 0,
    facing: cosFacing,
  };
}

/** Project into a `size × size` box, the globe filling it inside `padding`. */
export function projectToGlobeSvg(
  at: LatLng,
  centre: LatLng,
  size: number,
  padding = 0
): GlobePoint {
  const radius = size / 2 - padding;
  const unit = projectToGlobe(at, centre);
  return {
    ...unit,
    x: size / 2 + unit.x * radius,
    y: size / 2 + unit.y * radius,
  };
}

/**
 * A polyline as SVG path data, broken wherever it passes behind the globe.
 *
 * Each visible run becomes its own `M…L…` subpath. Joining across a hidden run would draw a
 * straight chord over the face of the globe — the single most obvious way to get an
 * orthographic map wrong.
 */
export function globePath(
  points: readonly LatLng[],
  centre: LatLng,
  size: number,
  padding = 0
): string {
  const parts: string[] = [];
  let drawing = false;
  for (const point of points) {
    const at = projectToGlobeSvg(point, centre, size, padding);
    if (!at.visible) {
      drawing = false;
      continue;
    }
    parts.push(`${drawing ? "L" : "M"}${at.x.toFixed(1)} ${at.y.toFixed(1)}`);
    drawing = true;
  }
  return parts.join("");
}

/** Decode one delta-encoded lon-first coastline run (see worldCoastline.ts). */
export function decodeCoastline(line: readonly number[], scale: number): LatLng[] {
  const out: LatLng[] = [];
  let lon = 0;
  let lat = 0;
  for (let index = 0; index < line.length; index += 2) {
    lon = index === 0 ? line[0]! : lon + line[index]!;
    lat = index === 0 ? line[1]! : lat + line[index + 1]!;
    out.push({ lat: lat / scale, lng: lon / scale });
  }
  return out;
}

/** Great-circle distance in kilometres. */
export function greatCircleKm(a: LatLng, b: LatLng): number {
  const dLat = (b.lat - a.lat) * RAD;
  const dLng = (b.lng - a.lng) * RAD;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a.lat * RAD) * Math.cos(b.lat * RAD) * Math.sin(dLng / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.min(1, Math.sqrt(h)));
}

export interface GroundSample extends LatLng {
  t_offset_s: number;
}

/** The ground track's samples that actually carry a position. */
export function groundTrack(
  track: readonly { t_offset_s: number; lat?: number | null; lng?: number | null }[]
): GroundSample[] {
  const out: GroundSample[] = [];
  for (const sample of track) {
    if (typeof sample.lat === "number" && typeof sample.lng === "number") {
      out.push({ t_offset_s: sample.t_offset_s, lat: sample.lat, lng: sample.lng });
    }
  }
  return out;
}

export interface ClosestApproach {
  at: GroundSample;
  km: number;
}

/**
 * Where the ground track passes nearest a place, and when.
 *
 * "The intersection with campus" — a satellite pass does not literally cross a point, so the
 * honest answer is the closest approach and its distance, not a claimed intersection.
 */
export function closestApproach(
  track: readonly GroundSample[],
  to: LatLng
): ClosestApproach | null {
  let best: ClosestApproach | null = null;
  for (const sample of track) {
    const km = greatCircleKm(sample, to);
    if (best === null || km < best.km) {
      best = { at: sample, km };
    }
  }
  return best;
}

/** Interpolate a ground position at a fraction through the track. */
export function groundAtFraction(
  track: readonly GroundSample[],
  fraction: number
): GroundSample | null {
  if (track.length === 0) return null;
  if (track.length === 1) return track[0]!;
  const clamped = Math.min(1, Math.max(0, fraction));
  const first = track[0]!;
  const last = track[track.length - 1]!;
  const span = last.t_offset_s - first.t_offset_s;
  if (span <= 0) return first;
  const target = first.t_offset_s + span * clamped;

  for (let index = 1; index < track.length; index += 1) {
    const previous = track[index - 1]!;
    const current = track[index]!;
    if (target <= current.t_offset_s) {
      const segment = current.t_offset_s - previous.t_offset_s;
      const t = segment <= 0 ? 0 : (target - previous.t_offset_s) / segment;
      return {
        t_offset_s: target,
        lat: previous.lat + (current.lat - previous.lat) * t,
        // Longitude the short way, so a pass crossing the antimeridian does not sweep
        // backwards around the planet.
        lng: previous.lng + shortestLngDelta(previous.lng, current.lng) * t,
      };
    }
  }
  return last;
}

/** Signed shortest delta from `from` to `to` in degrees of longitude. */
export function shortestLngDelta(from: number, to: number): number {
  let delta = (to - from) % 360;
  if (delta > 180) delta -= 360;
  if (delta < -180) delta += 360;
  return delta;
}
