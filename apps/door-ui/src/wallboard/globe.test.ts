import { describe, expect, it } from "vitest";
import {
  closestApproach,
  decodeCoastline,
  farthestSampleIndex,
  globePath,
  greatCircleKm,
  groundAtFraction,
  groundTrack,
  orbitAtTime,
  orbitGroundTrack,
  orbitLoopPoints,
  projectToGlobe,
  projectToGlobeSvg,
  shortestLngDelta,
} from "./globe";

/**
 * The orthographic projection, tested without a browser.
 *
 * The failure this guards is silent: half the sphere is behind the globe, and a far-side point
 * still projects to a plausible position on the near face. Without culling, Asia draws over
 * the Pacific and the satellite appears on the wrong side of the world, with nothing looking
 * broken.
 */

const CAMPUS = { lat: 37.422, lng: -122.172 };

describe("projectToGlobe", () => {
  it("puts the view centre at the middle of the disc", () => {
    const at = projectToGlobe(CAMPUS, CAMPUS);
    expect(at.x).toBeCloseTo(0, 6);
    expect(at.y).toBeCloseTo(0, 6);
    expect(at.visible).toBe(true);
    expect(at.facing).toBeCloseTo(1, 6);
  });

  it("hides the exact antipode", () => {
    const antipode = { lat: -CAMPUS.lat, lng: CAMPUS.lng + 180 };
    const at = projectToGlobe(antipode, CAMPUS);
    expect(at.visible).toBe(false);
    expect(at.facing).toBeLessThan(0);
  });

  it("puts a point 90 degrees away exactly on the limb", () => {
    const onLimb = projectToGlobe({ lat: 0, lng: CAMPUS.lng + 90 }, { lat: 0, lng: CAMPUS.lng });
    expect(Math.hypot(onLimb.x, onLimb.y)).toBeCloseTo(1, 6);
    expect(onLimb.facing).toBeCloseTo(0, 6);
  });

  it("never projects outside the unit disc", () => {
    for (let lat = -90; lat <= 90; lat += 15) {
      for (let lng = -180; lng <= 180; lng += 15) {
        const at = projectToGlobe({ lat, lng }, CAMPUS);
        expect(Math.hypot(at.x, at.y)).toBeLessThanOrEqual(1.000001);
      }
    }
  });

  it("orients north up and east right", () => {
    const north = projectToGlobe({ lat: CAMPUS.lat + 20, lng: CAMPUS.lng }, CAMPUS);
    expect(north.y).toBeLessThan(0);
    const east = projectToGlobe({ lat: CAMPUS.lat, lng: CAMPUS.lng + 20 }, CAMPUS);
    expect(east.x).toBeGreaterThan(0);
  });
});

describe("projectToGlobeSvg", () => {
  it("centres the globe and honours padding", () => {
    const centre = projectToGlobeSvg(CAMPUS, CAMPUS, 400, 20);
    expect(centre.x).toBeCloseTo(200, 6);
    expect(centre.y).toBeCloseTo(200, 6);

    const limb = projectToGlobeSvg({ lat: 0, lng: 90 }, { lat: 0, lng: 0 }, 400, 20);
    expect(limb.x).toBeCloseTo(380, 6);
  });
});

describe("globePath", () => {
  it("breaks the line where it passes behind the globe", () => {
    // A straight run along the equator from the near side, around the back, and out again.
    const points = [
      { lat: 0, lng: 0 },
      { lat: 0, lng: 60 },
      { lat: 0, lng: 170 }, // behind
      { lat: 0, lng: 190 }, // behind
      { lat: 0, lng: 300 },
    ];
    const path = globePath(points, { lat: 0, lng: 0 }, 200);
    // Two separate subpaths, not one chord straight across the face of the globe.
    expect((path.match(/M/g) ?? []).length).toBe(2);
  });

  it("returns nothing for a line entirely on the far side", () => {
    const path = globePath([{ lat: 0, lng: 175 }, { lat: 0, lng: 185 }], { lat: 0, lng: 0 }, 200);
    expect(path).toBe("");
  });
});

describe("decodeCoastline", () => {
  it("reads lon-first deltas back into lat/lng", () => {
    // lon 10.00, lat 20.00, then +1.00 lon, -2.00 lat
    const decoded = decodeCoastline([1000, 2000, 100, -200], 100);
    expect(decoded).toEqual([
      { lat: 20, lng: 10 },
      { lat: 18, lng: 11 },
    ]);
  });
});

describe("greatCircleKm", () => {
  it("is zero for a point against itself", () => {
    expect(greatCircleKm(CAMPUS, CAMPUS)).toBeCloseTo(0, 6);
  });

  it("matches a known distance", () => {
    // Stanford to San Francisco is about 50 km.
    const km = greatCircleKm(CAMPUS, { lat: 37.7749, lng: -122.4194 });
    expect(km).toBeGreaterThan(40);
    expect(km).toBeLessThan(60);
  });
});

describe("groundTrack", () => {
  it("keeps only the samples that carry a position", () => {
    // Older producers omit lat/lng entirely (ADR-0030), and a globe must not invent one.
    const track = groundTrack([
      { t_offset_s: 0, azimuth_deg: 1, elevation_deg: 1 } as never,
      { t_offset_s: 10, lat: 37, lng: -122 },
      { t_offset_s: 20, lat: null, lng: null },
    ]);
    expect(track).toEqual([{ t_offset_s: 10, lat: 37, lng: -122 }]);
  });
});

describe("closestApproach", () => {
  it("finds the nearest point on the track and when it happens", () => {
    const track = [
      { t_offset_s: 0, lat: 30, lng: -122 },
      { t_offset_s: 60, lat: 37.4, lng: -122.17 },
      { t_offset_s: 120, lat: 45, lng: -122 },
    ];
    const closest = closestApproach(track, CAMPUS)!;
    expect(closest.at.t_offset_s).toBe(60);
    expect(closest.km).toBeLessThan(10);
  });

  it("returns null for an empty track rather than a fake zero", () => {
    expect(closestApproach([], CAMPUS)).toBeNull();
  });
});

describe("groundAtFraction", () => {
  const track = [
    { t_offset_s: 0, lat: 0, lng: 0 },
    { t_offset_s: 100, lat: 10, lng: 10 },
  ];

  it("returns the endpoints at 0 and 1", () => {
    expect(groundAtFraction(track, 0)!.lat).toBeCloseTo(0, 6);
    expect(groundAtFraction(track, 1)!.lat).toBeCloseTo(10, 6);
  });

  it("interpolates in between", () => {
    const mid = groundAtFraction(track, 0.5)!;
    expect(mid.lat).toBeCloseTo(5, 6);
    expect(mid.lng).toBeCloseTo(5, 6);
  });

  it("crosses the antimeridian the short way", () => {
    // 175 -> -175 is 10 degrees east, not 350 backwards around the planet.
    const crossing = [
      { t_offset_s: 0, lat: 0, lng: 175 },
      { t_offset_s: 10, lat: 0, lng: -175 },
    ];
    const mid = groundAtFraction(crossing, 0.5)!;
    expect(Math.abs(mid.lng)).toBeCloseTo(180, 4);
  });

  it("handles degenerate tracks without throwing", () => {
    expect(groundAtFraction([], 0.5)).toBeNull();
    expect(groundAtFraction([{ t_offset_s: 0, lat: 1, lng: 2 }], 0.5)!.lat).toBe(1);
  });
});

describe("shortestLngDelta", () => {
  it("takes the short way in both directions", () => {
    expect(shortestLngDelta(175, -175)).toBeCloseTo(10, 6);
    expect(shortestLngDelta(-175, 175)).toBeCloseTo(-10, 6);
    expect(shortestLngDelta(0, 90)).toBeCloseTo(90, 6);
  });
});

describe("orbitGroundTrack", () => {
  it("parses absolute sample times into ms and keeps valid positions", () => {
    const track = orbitGroundTrack([
      { at: "2026-07-20T21:00:00Z", lat: 0, lng: -122 },
      { at: "2026-07-20T21:23:00Z", lat: 45, lng: -80 },
    ]);
    expect(track).toHaveLength(2);
    expect(track[0]!.atMs).toBe(Date.parse("2026-07-20T21:00:00Z"));
    expect(track[1]!.lat).toBe(45);
    // 23 minutes apart.
    expect(track[1]!.atMs - track[0]!.atMs).toBe(23 * 60_000);
  });
});

describe("farthestSampleIndex", () => {
  it("picks the sample deepest in the far hemisphere (smallest facing)", () => {
    // At the centre, the antipode, and points either side. The antipode is deepest.
    const points = [
      CAMPUS,
      { lat: 0, lng: CAMPUS.lng + 90 }, // limb
      { lat: -CAMPUS.lat, lng: CAMPUS.lng + 180 }, // antipode: most hidden
      { lat: 0, lng: CAMPUS.lng - 90 }, // limb
    ];
    expect(farthestSampleIndex(points, CAMPUS)).toBe(2);
  });
});

describe("orbitLoopPoints", () => {
  // One period of an inclined orbit that drifts westward as Earth turns — the shape the mock
  // provider and the real feed both emit (ADR-0041). Its first/last samples are ~23° of longitude
  // apart, so the loop does not close.
  const wrap180 = (deg: number) => (((((deg + 180) % 360) + 360) % 360) - 180);
  function mockOrbitTrack(inclDeg: number, nodeLng: number, periodMin: number, samples = 60) {
    const incl = (inclDeg * Math.PI) / 180;
    return Array.from({ length: samples + 1 }, (_, index) => {
      const frac = index / samples;
      const u = 2 * Math.PI * frac;
      const lat = (Math.asin(Math.sin(incl) * Math.sin(u)) * 180) / Math.PI;
      const lonOrbit = (Math.atan2(Math.cos(incl) * Math.sin(u), Math.cos(u)) * 180) / Math.PI;
      const rotation = 360 * frac * (periodMin / 1436);
      return {
        lat: Number(lat.toFixed(3)),
        lng: Number(wrap180(nodeLng + lonOrbit - rotation).toFixed(3)),
      };
    });
  }
  const countSubpaths = (d: string) => (d.match(/M/g) ?? []).length;

  it("hides the seam so a near-side seam becomes one continuous arc, ends behind the globe", () => {
    // ISS parameters from the door: the period seam falls (0, -45)/(0, -68), both on the NEAR face.
    const track = mockOrbitTrack(51.6, -45, 92.9);

    // Bug reproduction: in payload order the near hemisphere breaks into two stubs with dangling
    // ends mid-disc — the "line that randomly starts and ends".
    expect(countSubpaths(globePath(track, CAMPUS, 200))).toBe(2);

    const loop = orbitLoopPoints(track, CAMPUS);
    // After re-winding, the whole near hemisphere is one uninterrupted arc.
    expect(countSubpaths(globePath(loop, CAMPUS, 200))).toBe(1);
    // And the drawn polyline's two ends — the seam — are both on the far hemisphere (not drawn).
    expect(projectToGlobe(loop[0]!, CAMPUS).visible).toBe(false);
    expect(projectToGlobe(loop[loop.length - 1]!, CAMPUS).visible).toBe(false);
  });

  it("keeps a far-side seam hidden and the near arc single", () => {
    // HST parameters: the seam already falls on the far side. It must stay one clean near arc,
    // and the polyline must still begin and end behind the globe.
    const track = mockOrbitTrack(28.5, 10, 95.4);
    const loop = orbitLoopPoints(track, CAMPUS);
    expect(countSubpaths(globePath(loop, CAMPUS, 200))).toBe(1);
    expect(projectToGlobe(loop[0]!, CAMPUS).visible).toBe(false);
    expect(projectToGlobe(loop[loop.length - 1]!, CAMPUS).visible).toBe(false);
  });

  it("bridges the seam densely, so no drawn near-side segment is a long straight jump", () => {
    // The ~23° period gap is filled with interpolated points; every near-side step should be a
    // short chord like the rest of the track, not one long line across the disc.
    const track = mockOrbitTrack(51.6, -45, 92.9);
    const loop = orbitLoopPoints(track, CAMPUS);
    let maxStepPx = 0;
    for (let i = 1; i < loop.length; i += 1) {
      const a = projectToGlobeSvg(loop[i - 1]!, CAMPUS, 200, 16);
      const b = projectToGlobeSvg(loop[i]!, CAMPUS, 200, 16);
      if (a.visible && b.visible) {
        maxStepPx = Math.max(maxStepPx, Math.hypot(b.x - a.x, b.y - a.y));
      }
    }
    // Every visible-to-visible step stays small and even; the seam is not a giant chord.
    expect(maxStepPx).toBeLessThan(15);
  });

  it("returns degenerate tracks unchanged rather than throwing", () => {
    expect(orbitLoopPoints([], CAMPUS)).toEqual([]);
    expect(orbitLoopPoints([{ lat: 1, lng: 2 }], CAMPUS)).toEqual([{ lat: 1, lng: 2 }]);
  });
});

describe("orbitAtTime", () => {
  // One ~92-minute period sampled every 23 minutes; the last sample closes the loop.
  const t0 = Date.parse("2026-07-20T21:00:00Z");
  const period = 92 * 60_000;
  const track = orbitGroundTrack([
    { at: new Date(t0).toISOString(), lat: 0, lng: 0 },
    { at: new Date(t0 + period / 4).toISOString(), lat: 40, lng: 45 },
    { at: new Date(t0 + period / 2).toISOString(), lat: 0, lng: 90 },
    { at: new Date(t0 + (3 * period) / 4).toISOString(), lat: -40, lng: 135 },
    { at: new Date(t0 + period).toISOString(), lat: 0, lng: 180 },
  ]);

  it("picks the sample the real clock lands on", () => {
    const at = orbitAtTime(track, t0 + period / 2)!;
    expect(at.lat).toBeCloseTo(0, 6);
    expect(at.lng).toBeCloseTo(90, 6);
  });

  it("interpolates between samples", () => {
    const at = orbitAtTime(track, t0 + period / 8)!;
    // Halfway between sample 0 (0,0) and sample 1 (40,45).
    expect(at.lat).toBeCloseTo(20, 6);
    expect(at.lng).toBeCloseTo(22.5, 6);
  });

  it("wraps a now past the end back into the period", () => {
    // now one and a half periods on == half a period in.
    const wrapped = orbitAtTime(track, t0 + period + period / 2)!;
    const direct = orbitAtTime(track, t0 + period / 2)!;
    expect(wrapped.lat).toBeCloseTo(direct.lat, 6);
    expect(wrapped.lng).toBeCloseTo(direct.lng, 6);
  });

  it("wraps a now before the first sample", () => {
    // A quarter-period before t0 is three-quarters of the way through the loop.
    const before = orbitAtTime(track, t0 - period / 4)!;
    const equiv = orbitAtTime(track, t0 + (3 * period) / 4)!;
    expect(before.lat).toBeCloseTo(equiv.lat, 6);
    expect(before.lng).toBeCloseTo(equiv.lng, 6);
  });

  it("crosses the antimeridian the short way", () => {
    const crossing = orbitGroundTrack([
      { at: new Date(t0).toISOString(), lat: 0, lng: 175 },
      { at: new Date(t0 + period).toISOString(), lat: 0, lng: -175 },
    ]);
    // Midway is at ±180, not sweeping 350 degrees back across the planet.
    const mid = orbitAtTime(crossing, t0 + period / 2)!;
    expect(Math.abs(mid.lng)).toBeCloseTo(180, 4);
  });

  it("handles degenerate tracks without throwing", () => {
    expect(orbitAtTime([], Date.now())).toBeNull();
    const one = orbitGroundTrack([{ at: new Date(t0).toISOString(), lat: 5, lng: 6 }]);
    expect(orbitAtTime(one, Date.now())!.lat).toBe(5);
  });
});
