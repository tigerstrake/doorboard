// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CampusDiningMap } from "./CampusDiningMap";
import { DINING_HALLS, projectToCampus } from "./campusMap";

/**
 * What the panel asserts on screen. The projection itself is covered in campusMap.test.ts;
 * this is about the panel not claiming to know a location it does not.
 */

// No global cleanup is configured in this package, and every test renders the same map.
afterEach(cleanup);

/** The hall names drawn on the map, as distinct from the same names in the note below it. */
const mapLabels = () =>
  Array.from(document.querySelectorAll(".campus-map__hall-label")).map((node) =>
    node.textContent?.trim()
  );

describe("CampusDiningMap", () => {
  it("marks the recommended hall", () => {
    render(<CampusDiningMap hall="Lakeside" title="Lakeside — dinner" backupHall="Stern" />);
    expect(screen.getByTestId("campus-map-pick")).toBeTruthy();
    // By class, because the hall name appears twice: on the map and in the note below it.
    expect(mapLabels()).toContain("Lakeside");
    // The backup is labelled too, so "or that one" is visible without reading the detail line.
    expect(mapLabels()).toContain("Stern");
  });

  it("puts the marker at the hall's projected position", () => {
    render(<CampusDiningMap hall="Wilbur" />);
    const dot = screen.getByTestId("campus-map-pick").querySelector(".campus-map__dot")!;
    const wilbur = DINING_HALLS.find((hall) => hall.name === "Wilbur")!;
    // 460 wide, matching the component; a regression in either would move the dot.
    const expected = projectToCampus(wilbur.at, 460, Math.round(460 / (462.6 / 250)));
    // Compare only x, which depends on width alone — the height is the component's business.
    expect(Number(dot.getAttribute("cx"))).toBeCloseTo(expected.x, 0);
  });

  it("falls back to the prose title when the event has no hall field", () => {
    // Events cached before ADR-0026 carry only "{hall} — {meal}".
    render(<CampusDiningMap hall={null} title="Ricker — brunch" />);
    expect(screen.getByTestId("campus-map-pick")).toBeTruthy();
    expect(mapLabels()).toContain("Ricker");
  });

  it("says so rather than guessing when the hall is unknown", () => {
    render(<CampusDiningMap hall="Somewhere New" title="Somewhere New — dinner" />);
    expect(screen.queryByTestId("campus-map-pick")).toBeNull();
    expect(screen.getByText(/not in the campus catalogue/i)).toBeTruthy();
  });

  it("still draws the campus when there is no hall at all", () => {
    // A manual recommendation names a dish, not a venue. The channel must not go blank.
    render(<CampusDiningMap hall={null} title="Noodle soup" />);
    expect(screen.getByTestId("campus-dining-map")).toBeTruthy();
    expect(screen.queryByTestId("campus-map-pick")).toBeNull();
  });

  it("admits when the point is the residence building, not the dining room", () => {
    render(<CampusDiningMap hall="Branner" />);
    expect(screen.getByText(/dining room is inside this building/i)).toBeTruthy();
  });
});
