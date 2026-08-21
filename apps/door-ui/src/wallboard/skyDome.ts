/**
 * Projecting a satellite pass onto a sky dome, and animating along it.
 *
 * A pass is not a place on the ground, it is a direction to look — so the flights map's
 * ground projection is the wrong picture for it. This is the conventional polar plot
 * instead: **azimuth around the circle, elevation radial with the horizon at the rim and
 * the zenith at the centre.** North is up, east to the right, matching how you would hold
 * a star chart over your head.
 *
 * Pure geometry, deliberately DOM-free: the panel renders SVG from these numbers, and the
 * projection is worth testing without standing up a browser.
 */

export interface TrackSample {
  t_offset_s: number;
  azimuth_deg: number;
  elevation_deg: number;
}

export interface Point {
  x: number;
  y: number;
}

/** Compass labels at the rim, in drawing order from north clockwise. */
export const COMPASS_POINTS: ReadonlyArray<{ label: string; azimuth: number }> = [
  { label: "N", azimuth: 0 },
  { label: "E", azimuth: 90 },
  { label: "S", azimuth: 180 },
  { label: "W", azimuth: 270 },
];

/**
 * Project an azimuth/elevation pair into unit dome coordinates.
 *
 * Returns x/y in [-1, 1] with the origin at the zenith. Radius is `1 - elevation/90`, so
 * the horizon lands on the unit circle and straight overhead is dead centre. Elevation is
 * clamped: a pass sampled at its endpoints can round a hair below zero, and a radius above
 * 1 would draw the satellite outside the sky.
 */
export function projectToDome(azimuthDeg: number, elevationDeg: number): Point {
  const elevation = Math.min(90, Math.max(0, elevationDeg));
  const radius = 1 - elevation / 90;
  // Screen y grows downward, so north (azimuth 0) must map to -y to appear at the top.
  const radians = (azimuthDeg * Math.PI) / 180;
  return { x: radius * Math.sin(radians), y: -radius * Math.cos(radians) };
}

/** Project into pixel space for an SVG of the given size. */
export function projectToSvg(
  azimuthDeg: number,
  elevationDeg: number,
  size: number,
  padding = 0
): Point {
  const usable = size / 2 - padding;
  const unit = projectToDome(azimuthDeg, elevationDeg);
  return { x: size / 2 + unit.x * usable, y: size / 2 + unit.y * usable };
}

/** An SVG path through the whole track, or null when there is no arc to draw. */
export function trackPath(
  track: readonly TrackSample[],
  size: number,
  padding = 0
): string | null {
  if (track.length < 2) return null;
  return track
    .map((sample, index) => {
      const { x, y } = projectToSvg(sample.azimuth_deg, sample.elevation_deg, size, padding);
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

export type PassPhase = "before" | "during" | "after";

export interface PassProgress {
  phase: PassPhase;
  /** 0 at rise, 1 at set. Clamped, so it is safe to interpolate with. */
  fraction: number;
  secondsUntilRise: number;
  secondsUntilSet: number;
}

/**
 * Where `now` sits relative to a pass.
 *
 * Split from the interpolation so the panel can decide *what* to say — counting down to a
 * rise, tracking a pass in progress, or reporting one that is over — without recomputing
 * time arithmetic in three places.
 */
export function passProgress(
  riseAtMs: number,
  setAtMs: number | null,
  nowMs: number
): PassProgress {
  if (setAtMs === null || setAtMs <= riseAtMs) {
    // No usable duration: treat it as a point in time, which is all the older payload
    // shape could tell us anyway.
    return {
      phase: nowMs < riseAtMs ? "before" : "after",
      fraction: nowMs < riseAtMs ? 0 : 1,
      secondsUntilRise: (riseAtMs - nowMs) / 1000,
      secondsUntilSet: 0,
    };
  }
  const span = setAtMs - riseAtMs;
  const raw = (nowMs - riseAtMs) / span;
  const phase: PassPhase = raw < 0 ? "before" : raw > 1 ? "after" : "during";
  return {
    phase,
    fraction: Math.min(1, Math.max(0, raw)),
    secondsUntilRise: (riseAtMs - nowMs) / 1000,
    secondsUntilSet: (setAtMs - nowMs) / 1000,
  };
}

/**
 * The satellite's position at a fraction along the pass, interpolated between samples.
 *
 * Azimuth is interpolated the short way around the compass, so a track crossing north
 * (350° → 10°) sweeps 20° through zero instead of 340° backwards across the whole sky.
 */
export function positionAtFraction(
  track: readonly TrackSample[],
  fraction: number
): TrackSample | null {
  if (track.length === 0) return null;
  if (track.length === 1) return track[0]!;

  const clamped = Math.min(1, Math.max(0, fraction));
  const first = track[0]!;
  const last = track[track.length - 1]!;
  const span = last.t_offset_s - first.t_offset_s;
  if (span <= 0) return first;

  const targetOffset = first.t_offset_s + span * clamped;
  let index = 0;
  while (index < track.length - 2 && track[index + 1]!.t_offset_s < targetOffset) index += 1;

  const a = track[index]!;
  const b = track[index + 1]!;
  const segment = b.t_offset_s - a.t_offset_s;
  const t = segment <= 0 ? 0 : (targetOffset - a.t_offset_s) / segment;

  let delta = b.azimuth_deg - a.azimuth_deg;
  if (delta > 180) delta -= 360;
  if (delta < -180) delta += 360;

  return {
    t_offset_s: targetOffset,
    azimuth_deg: (a.azimuth_deg + delta * t + 360) % 360,
    elevation_deg: a.elevation_deg + (b.elevation_deg - a.elevation_deg) * t,
  };
}

/** "in 4m 20s" / "12s ago", for a countdown that reads as prose. */
export function humanizeSeconds(seconds: number): string {
  const abs = Math.abs(Math.round(seconds));
  const minutes = Math.floor(abs / 60);
  const rest = abs % 60;
  const text = minutes > 0 ? `${minutes}m ${rest}s` : `${rest}s`;
  return seconds >= 0 ? `in ${text}` : `${text} ago`;
}
