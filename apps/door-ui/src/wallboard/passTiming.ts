/**
 * Pass timing: how far through a pass we are, and how long until the next one.
 *
 * Extracted from the old `skyDome.ts` when the satellite panel became a globe (ADR-0030).
 * The dome's az/el projection went with the panel that used it; these two are about *time*
 * and are shared by whatever draws the pass.
 */

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

/** "in 4m 20s" / "12s ago", for a countdown that reads as prose. */
export function humanizeSeconds(seconds: number): string {
  const abs = Math.abs(Math.round(seconds));
  const minutes = Math.floor(abs / 60);
  const rest = abs % 60;
  const text = minutes > 0 ? `${minutes}m ${rest}s` : `${rest}s`;
  return seconds >= 0 ? `in ${text}` : `${text} ago`;
}
