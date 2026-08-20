import { describe, expect, it } from "vitest";
import {
  CAMPUS_ASPECT,
  CAMPUS_VIEW,
  DINING_HALLS,
  DOORBOARD_AT,
  SAME_BUILDING_M,
  metresBetween,
  hallFromTitle,
  projectToCampus,
  resolveDiningHall,
  withinCampusView,
} from "./campusMap";

/**
 * The campus projection and hall catalogue, tested without a browser.
 *
 * The failure this guards against is not a crash — it is a confident dot on the wrong side
 * of campus. Rendering the component with a stub map would prove the SVG exists and nothing
 * about whether Wilbur is east of the lake.
 */

describe("projectToCampus", () => {
  const W = 400;
  const H = 250;

  it("puts the view corners at the box corners", () => {
    const nw = projectToCampus({ lat: CAMPUS_VIEW.north, lng: CAMPUS_VIEW.west }, W, H);
    expect(nw.x).toBeCloseTo(0, 6);
    expect(nw.y).toBeCloseTo(0, 6);

    const se = projectToCampus({ lat: CAMPUS_VIEW.south, lng: CAMPUS_VIEW.east }, W, H);
    expect(se.x).toBeCloseTo(W, 6);
    expect(se.y).toBeCloseTo(H, 6);
  });

  it("puts north up: a higher latitude is a smaller y", () => {
    // Screen y grows downward, so getting this backwards mirrors the whole map.
    const north = projectToCampus({ lat: 37.428, lng: -122.17 }, W, H);
    const south = projectToCampus({ lat: 37.42, lng: -122.17 }, W, H);
    expect(north.y).toBeLessThan(south.y);
  });

  it("puts east right", () => {
    const east = projectToCampus({ lat: 37.424, lng: -122.163 }, W, H);
    const west = projectToCampus({ lat: 37.424, lng: -122.18 }, W, H);
    expect(east.x).toBeGreaterThan(west.x);
  });

  it("does not distort: equal ground distances are equal pixel distances", () => {
    // 0.001° of latitude is ~111 m; the same ground distance in longitude is ~0.00126°.
    const box = { w: 400, h: 400 / CAMPUS_ASPECT };
    const origin = projectToCampus({ lat: 37.424, lng: -122.171 }, box.w, box.h);
    const northOf = projectToCampus({ lat: 37.425, lng: -122.171 }, box.w, box.h);
    const eastOf = projectToCampus(
      { lat: 37.424, lng: -122.171 + 0.001 / Math.cos((37.424 * Math.PI) / 180) },
      box.w,
      box.h
    );
    const dNorth = Math.abs(northOf.y - origin.y);
    const dEast = Math.abs(eastOf.x - origin.x);
    expect(dEast).toBeCloseTo(dNorth, 1);
  });
});

describe("the hall catalogue", () => {
  it("keeps every hall inside the framed view", () => {
    // A hall outside the box would be drawn clipped or off-canvas, silently.
    for (const hall of DINING_HALLS) {
      expect(withinCampusView(hall.at), `${hall.name} is outside the view`).toBe(true);
    }
  });

  it("frames the doorboard itself", () => {
    expect(withinCampusView(DOORBOARD_AT)).toBe(true);
  });

  it("puts the doorboard at Florence Moore, where the door actually is", () => {
    // It was the Main Quad — the campus centroid — which drew every walk line from a
    // kilometre away from the door. FloMo has its own dining hall, so the nearest hall to
    // the door is FloMo's, and that has to come out as "the same building".
    const flomo = DINING_HALLS.find((hall) => hall.name === "Florence Moore")!;
    expect(metresBetween(DOORBOARD_AT, flomo.at)).toBeLessThan(SAME_BUILDING_M);

    // And somewhere genuinely across campus is not.
    const wilbur = DINING_HALLS.find((hall) => hall.name === "Wilbur")!;
    expect(metresBetween(DOORBOARD_AT, wilbur.at)).toBeGreaterThan(600);
  });

  it("has no duplicate names or aliases", () => {
    const names = DINING_HALLS.map((hall) => hall.name);
    expect(new Set(names).size).toBe(names.length);
    const aliases = DINING_HALLS.flatMap((hall) => hall.aliases);
    expect(new Set(aliases).size).toBe(aliases.length);
  });

  it("agrees with known campus geography", () => {
    // Sanity checks a projection bug or a transposed coordinate would fail. Wilbur and
    // Stern are east campus; Ricker is out past the lake to the west.
    const at = (name: string) => DINING_HALLS.find((hall) => hall.name === name)!.at;
    expect(at("Wilbur").lng).toBeGreaterThan(at("Stern").lng);
    expect(at("Ricker").lng).toBeLessThan(at("Lakeside").lng);
    expect(at("Lakeside").lng).toBeLessThan(at("Stern").lng);
    expect(at("Florence Moore").lat).toBeLessThan(at("Arrillaga Family Dining Commons").lat);
  });
});

/**
 * The nine hall names in Stanford's own menu-site location dropdown, read from
 * rdeapps.stanford.edu on 2026-08-17. These are the exact strings the provider publishes,
 * so this list — not our guesses at them — is what the catalogue has to cover.
 *
 * If the site renames a hall this test keeps passing while the map silently stops finding
 * it, so it is a floor, not a guarantee. It still catches the failure that actually
 * happened: a hall we never thought of at all (EVGR).
 */
const LIVE_HALL_NAMES = [
  "Arrillaga Family Dining Commons",
  "Branner Dining",
  "EVGR Dining",
  "Florence Moore Dining",
  "Gerhard Casper Dining",
  "Lakeside Dining",
  "Ricker Dining",
  "Stern Dining",
  "Wilbur Dining",
] as const;

describe("resolveDiningHall", () => {
  it("resolves every hall the menu site actually offers", () => {
    for (const name of LIVE_HALL_NAMES) {
      expect(resolveDiningHall(name), `${name} is not in the catalogue`).not.toBeNull();
    }
  });

  it("maps each live name to a distinct hall", () => {
    // Two site names collapsing onto one catalogue entry would put both halls' dots in one
    // place, which looks like success.
    const resolvedNames = LIVE_HALL_NAMES.map((name) => resolveDiningHall(name)!.name);
    expect(new Set(resolvedNames).size).toBe(LIVE_HALL_NAMES.length);
  });

  it("matches the provider's own wording", () => {
    expect(resolveDiningHall("Wilbur")?.name).toBe("Wilbur");
    expect(resolveDiningHall("Wilbur Dining")?.name).toBe("Wilbur");
    expect(resolveDiningHall("STERN DINING HALL")?.name).toBe("Stern");
    expect(resolveDiningHall("Arrillaga Family Dining Commons")?.name).toBe(
      "Arrillaga Family Dining Commons"
    );
    expect(resolveDiningHall("FloMo")?.name).toBe("Florence Moore");
  });

  it("returns null rather than guessing", () => {
    // An unknown hall must fail visibly: the panel then draws no point at all.
    expect(resolveDiningHall("Some New Dining Hall")).toBeNull();
    expect(resolveDiningHall("")).toBeNull();
    expect(resolveDiningHall(null)).toBeNull();
    expect(resolveDiningHall(undefined)).toBeNull();
  });

  it("prefers the longest alias", () => {
    // "florence" alone would also match; the specific alias must win.
    expect(resolveDiningHall("Florence Moore Dining")?.name).toBe("Florence Moore");
  });
});

describe("hallFromTitle", () => {
  it("recovers the hall from the prose title older events carry", () => {
    expect(hallFromTitle("Wilbur — dinner")).toBe("Wilbur");
    expect(hallFromTitle("Arrillaga Family Dining Commons — lunch")).toBe(
      "Arrillaga Family Dining Commons"
    );
    // A hyphen instead of an em dash, and a hall name that itself contains no separator.
    expect(hallFromTitle("Stern - brunch")).toBe("Stern");
  });

  it("degrades to null on nothing useful", () => {
    expect(hallFromTitle(null)).toBeNull();
    expect(hallFromTitle("")).toBeNull();
    expect(hallFromTitle("   ")).toBeNull();
  });
});
