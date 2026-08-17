import { describe, expect, it } from "vitest";
import {
  humanizeSeconds,
  passProgress,
  positionAtFraction,
  projectToDome,
  projectToSvg,
  trackPath,
} from "./skyDome";
import type { TrackSample } from "./skyDome";

/**
 * The sky-dome projection, tested without a browser.
 *
 * The convention is load-bearing and easy to get subtly wrong: horizon at the rim, zenith
 * at the centre, north up, east right. Flip a sign and the panel confidently tells someone
 * to look the opposite way, which is the sort of wrong-but-plausible output this project has
 * been bitten by before.
 */

const sample = (t: number, az: number, el: number): TrackSample => ({
  t_offset_s: t,
  azimuth_deg: az,
  elevation_deg: el,
});

describe("projectToDome", () => {
  it("puts the zenith at the centre", () => {
    const at = projectToDome(0, 90);
    expect(at.x).toBeCloseTo(0, 6);
    expect(at.y).toBeCloseTo(0, 6);
  });

  it("puts the horizon on the unit circle", () => {
    for (const azimuth of [0, 90, 180, 270, 45]) {
      const at = projectToDome(azimuth, 0);
      expect(Math.hypot(at.x, at.y)).toBeCloseTo(1, 6);
    }
  });

  it("orients north up and east right", () => {
    // Screen y grows downward, so north must be negative y or the whole plot is mirrored.
    const north = projectToDome(0, 0);
    expect(north.y).toBeLessThan(0);
    expect(north.x).toBeCloseTo(0, 6);

    const east = projectToDome(90, 0);
    expect(east.x).toBeGreaterThan(0);
    expect(east.y).toBeCloseTo(0, 6);

    const south = projectToDome(180, 0);
    expect(south.y).toBeGreaterThan(0);

    const west = projectToDome(270, 0);
    expect(west.x).toBeLessThan(0);
  });

  it("clamps a sub-horizon elevation instead of drawing outside the sky", () => {
    // Endpoint rounding in the provider can put a sample a hair below zero.
    const at = projectToDome(120, -3);
    expect(Math.hypot(at.x, at.y)).toBeLessThanOrEqual(1);
  });

  it("clamps above the zenith too", () => {
    const at = projectToDome(120, 140);
    expect(Math.hypot(at.x, at.y)).toBeCloseTo(0, 6);
  });
});

describe("projectToSvg", () => {
  it("centres the dome in the viewport and respects padding", () => {
    const centre = projectToSvg(0, 90, 320, 20);
    expect(centre.x).toBeCloseTo(160, 6);
    expect(centre.y).toBeCloseTo(160, 6);

    const northHorizon = projectToSvg(0, 0, 320, 20);
    expect(northHorizon.y).toBeCloseTo(20, 6);
  });
});

describe("trackPath", () => {
  it("returns null when there is no arc to draw", () => {
    // A panel must not invent a shape from one point.
    expect(trackPath([], 320)).toBeNull();
    expect(trackPath([sample(0, 90, 10)], 320)).toBeNull();
  });

  it("moves once and then lines through every later sample", () => {
    const path = trackPath([sample(0, 90, 0), sample(30, 135, 40), sample(60, 180, 0)], 320, 20);
    expect(path).not.toBeNull();
    expect(path!.startsWith("M")).toBe(true);
    expect((path!.match(/L/g) ?? []).length).toBe(2);
  });
});

describe("passProgress", () => {
  const rise = 1000;
  const set = 5000;

  it("counts down before a pass", () => {
    const p = passProgress(rise, set, 0);
    expect(p.phase).toBe("before");
    expect(p.fraction).toBe(0);
    expect(p.secondsUntilRise).toBeCloseTo(1, 6);
  });

  it("tracks the elapsed fraction during a pass", () => {
    const p = passProgress(rise, set, 3000);
    expect(p.phase).toBe("during");
    expect(p.fraction).toBeCloseTo(0.5, 6);
  });

  it("reports a finished pass without exceeding 1", () => {
    const p = passProgress(rise, set, 99999);
    expect(p.phase).toBe("after");
    expect(p.fraction).toBe(1);
  });

  it("degrades gracefully when set_at is absent", () => {
    // The older payload shape carried only a rise time; it must not divide by zero.
    const before = passProgress(rise, null, 0);
    expect(before.phase).toBe("before");
    const after = passProgress(rise, null, 2000);
    expect(after.phase).toBe("after");
  });

  it("treats a non-positive duration as a point in time", () => {
    const p = passProgress(rise, rise, 2000);
    expect(p.phase).toBe("after");
    expect(Number.isFinite(p.fraction)).toBe(true);
  });
});

describe("positionAtFraction", () => {
  const track = [sample(0, 90, 0), sample(50, 135, 50), sample(100, 180, 0)];

  it("returns the endpoints at 0 and 1", () => {
    expect(positionAtFraction(track, 0)!.azimuth_deg).toBeCloseTo(90, 6);
    expect(positionAtFraction(track, 1)!.azimuth_deg).toBeCloseTo(180, 6);
  });

  it("interpolates between samples", () => {
    const mid = positionAtFraction(track, 0.25)!;
    expect(mid.azimuth_deg).toBeCloseTo(112.5, 4);
    expect(mid.elevation_deg).toBeCloseTo(25, 4);
  });

  it("takes the short way around north", () => {
    // 350° → 10° is a 20° sweep through zero, not 340° backwards across the whole sky.
    const crossing = [sample(0, 350, 10), sample(10, 10, 20)];
    const mid = positionAtFraction(crossing, 0.5)!;
    expect(mid.azimuth_deg).toBeCloseTo(0, 4);
  });

  it("clamps out-of-range fractions", () => {
    expect(positionAtFraction(track, -5)!.azimuth_deg).toBeCloseTo(90, 6);
    expect(positionAtFraction(track, 5)!.azimuth_deg).toBeCloseTo(180, 6);
  });

  it("handles degenerate tracks without throwing", () => {
    expect(positionAtFraction([], 0.5)).toBeNull();
    expect(positionAtFraction([sample(0, 42, 7)], 0.5)!.azimuth_deg).toBe(42);
    // Zero-span track: every sample at the same offset.
    expect(positionAtFraction([sample(0, 42, 7), sample(0, 99, 9)], 0.5)!.azimuth_deg).toBe(42);
  });
});

describe("humanizeSeconds", () => {
  it("reads as prose in both directions", () => {
    expect(humanizeSeconds(260)).toBe("in 4m 20s");
    expect(humanizeSeconds(12)).toBe("in 12s");
    expect(humanizeSeconds(-12)).toBe("12s ago");
  });
});
