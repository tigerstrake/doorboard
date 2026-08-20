import { describe, expect, it } from "vitest";
import {
  closestApproach,
  decodeCoastline,
  globePath,
  greatCircleKm,
  groundAtFraction,
  groundTrack,
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
