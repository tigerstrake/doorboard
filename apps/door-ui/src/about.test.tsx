// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { AdminAboutPanel } from "./AdminAboutPanel";
import { aboutFixture } from "./fixtures";

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
