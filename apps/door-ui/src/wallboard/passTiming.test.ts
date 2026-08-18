import { describe, expect, it } from "vitest";
import { humanizeSeconds, passProgress } from "./passTiming";

/**
 * Pass timing, kept from the sky-dome suite when the panel became a globe (ADR-0030). The
 * dome projection tests went with the dome; these are about time and still apply.
 */

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
describe("humanizeSeconds", () => {
  it("reads as prose in both directions", () => {
    expect(humanizeSeconds(260)).toBe("in 4m 20s");
    expect(humanizeSeconds(12)).toBe("in 12s");
    expect(humanizeSeconds(-12)).toBe("12s ago");
  });
});