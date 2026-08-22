// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { AdminAboutPanel } from "./AdminAboutPanel";
import { aboutFixture } from "./fixtures";
import { ABOUT_FACTS, AboutDoorboard } from "./AboutDoorboard";

afterEach(cleanup);

describe("About this project (T-608)", () => {
  it("renders the project name, tagline, and description", () => {
    render(<AdminAboutPanel />);
    expect(screen.getByText("About Doorboard")).toBeTruthy();
    expect(screen.getByText(aboutFixture.tagline)).toBeTruthy();
    expect(screen.getByText(/coordinated set of AI agents/)).toBeTruthy();
  });

  it("shows the line-of-code total and every language in the breakdown", () => {
    render(<AdminAboutPanel />);
    expect(screen.getByText(aboutFixture.stats.lines_of_code.toLocaleString())).toBeTruthy();
    for (const lang of aboutFixture.stats.languages) {
      expect(screen.getAllByText(lang.name).length).toBeGreaterThan(0);
    }
  });

  it("lists the structural counts", () => {
    render(<AdminAboutPanel />);
    expect(screen.getByText("Contract event types")).toBeTruthy();
    expect(
      screen.getByText(aboutFixture.stats.counts.contract_event_types.toLocaleString())
    ).toBeTruthy();
  });

  it("has non-empty generated stats (guards against a broken JSON regen)", () => {
    expect(aboutFixture.stats.lines_of_code).toBeGreaterThan(0);
    expect(aboutFixture.stats.languages.length).toBeGreaterThan(0);
    expect(aboutFixture.stats.counts.services).toBeGreaterThan(0);
    expect(aboutFixture.stats.generated_at).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe("the focused About view carries the numbers too", () => {
  it("shows facts only when asked, so the 7in doorpad screen stays about the visitor", () => {
    // The doorpad version is a scrolling column where extra material buries the part a
    // visitor actually needs: what is recorded and how to erase it.
    render(<AboutDoorboard />);
    expect(screen.queryByTestId("about-facts")).toBeNull();

    cleanup();
    render(<AboutDoorboard showFacts />);
    expect(screen.getByTestId("about-facts")).toBeTruthy();
    expect(screen.getByTestId("about-tech-facts")).toBeTruthy();
  });

  it("renders every fact from the generated stats, not hand-typed numbers", () => {
    render(<AboutDoorboard showFacts />);
    const values = Array.from(
      screen.getByTestId("about-facts").querySelectorAll(".about-facts__value")
    ).map((n) => n.textContent);
    expect(values).toHaveLength(ABOUT_FACTS.length);
    // Every one is a formatted number. A label that lost its value would read as a bug.
    for (const value of values) {
      expect(value).toMatch(/^[\d,]+$/);
    }
  });

  it("keeps the privacy invariants ahead of the trivia", () => {
    // Ordering matters: someone standing at a door wants "what does this record about me"
    // before "how many lines of code is it".
    render(<AboutDoorboard showFacts />);
    const text = screen.getByTestId("about-doorboard").textContent ?? "";
    expect(text.indexOf("not identified")).toBeGreaterThan(-1);
    expect(text.indexOf("not identified")).toBeLessThan(text.indexOf("By the numbers"));
  });
});

it("discloses that a name can be spoken aloud, not just shown", () => {
  // ADR-0034: a screen at arm's length shows a name to the person; a speaker
  // tells the hallway. Without this section the About text's "changes a greeting
  // and a colour" would be the whole story, and it isn't.
  render(<AboutDoorboard showFacts />);
  expect(screen.getByText("If the door speaks your name")).toBeTruthy();
  expect(screen.getByText(/tells everyone in the hallway/)).toBeTruthy();
  expect(screen.getByText(/separate choice from enrolling/)).toBeTruthy();
  expect(screen.getByText(/stays quiet overnight/)).toBeTruthy();
});
