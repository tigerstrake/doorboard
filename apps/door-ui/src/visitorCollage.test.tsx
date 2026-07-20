// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { VisitorCollageContent, WallboardFocusedView } from "./wallboardChannels";
import type { VisitorCollage } from "./wallboardChannels";

afterEach(() => cleanup());

const COLLAGE: VisitorCollage = {
  stats: {
    total_checkins: 12,
    checkins_this_year: 9,
    unique_visitors: 5,
    distinct_visitors: 3,
    guest_count: 2,
    most_frequent: { label: "Alex", count: 4 },
    first_checkin_at: "2026-01-02T10:00:00Z",
    most_recent_checkin_at: "2026-07-18T18:30:00Z",
  },
  photos: [
    { recording_id: "rec_1", thumbnail_path: null, label: "Alex", created_at: "2026-07-18T18:30:00Z" },
    { recording_id: "rec_2", thumbnail_path: null, label: "Sam", created_at: "2026-07-10T12:00:00Z" },
  ],
};

function grid(): HTMLElement {
  return document.querySelector(".visitor-collage-grid") as HTMLElement;
}

function statsText(): string {
  return (document.querySelector(".visitor-collage-stats") as HTMLElement).textContent ?? "";
}

describe("visitor collage tile", () => {
  it("renders the approved check-in photos with visitor labels", () => {
    render(<VisitorCollageContent collage={COLLAGE} />);
    expect(within(grid()).getByText("Alex")).toBeTruthy();
    expect(within(grid()).getByText("Sam")).toBeTruthy();
    // No servable URL -> initial-based placeholders, not <img> elements.
    expect(document.querySelectorAll(".visitor-collage-thumb--placeholder").length).toBe(2);
    expect(document.querySelector("img.visitor-collage-thumb")).toBeNull();
  });

  it("renders the fun-stats panel", () => {
    render(<VisitorCollageContent collage={COLLAGE} />);
    const text = statsText();
    expect(text).toContain("5 visitor"); // unique visitors
    expect(text).toContain("9 this year");
    expect(text).toContain("12 total visits");
    expect(text).toContain("Most frequent");
    expect(text).toContain("Alex");
    expect(text).toContain("(4)"); // most-frequent visit count
    expect(text).toContain("Last visit");
  });

  it("renders an <img> only when the thumbnail is an absolute URL", () => {
    const withUrl: VisitorCollage = {
      ...COLLAGE,
      photos: [
        {
          recording_id: "rec_url",
          thumbnail_path: "https://nas.local/thumbs/rec_url.jpg",
          label: "Jordan",
          created_at: "2026-07-01T00:00:00Z",
        },
      ],
    };
    render(<VisitorCollageContent collage={withUrl} />);
    const img = document.querySelector("img.visitor-collage-thumb") as HTMLImageElement | null;
    expect(img).toBeTruthy();
    expect(img?.getAttribute("src")).toContain("rec_url.jpg");
    expect(document.querySelector(".visitor-collage-thumb--placeholder")).toBeNull();
  });

  it("shows the stats but a photo notice when there are check-ins yet no approved photos", () => {
    const statsOnly: VisitorCollage = { stats: COLLAGE.stats, photos: [] };
    render(<VisitorCollageContent collage={statsOnly} />);
    expect(screen.getByText(/Approved check-in photos will appear here/)).toBeTruthy();
    expect(statsText()).toContain("5 visitor"); // stats still render
  });

  it("shows the graceful empty state when nobody has checked in", () => {
    const empty: VisitorCollage = {
      stats: {
        total_checkins: 0,
        checkins_this_year: 0,
        unique_visitors: 0,
        distinct_visitors: 0,
        guest_count: 0,
        most_frequent: null,
        first_checkin_at: null,
        most_recent_checkin_at: null,
      },
      photos: [],
    };
    render(<VisitorCollageContent collage={empty} />);
    expect(screen.getByText(/No check-ins yet/)).toBeTruthy();
  });

  it("shows the empty state when the collage feed is unavailable", () => {
    render(<VisitorCollageContent collage={null} />);
    expect(screen.getByText(/No check-ins yet/)).toBeTruthy();
  });
});

describe("visitor collage focused view", () => {
  function renderFocus(collage: VisitorCollage | null) {
    return render(
      <WallboardFocusedView
        channel="visitors"
        poll={null}
        pollResults={null}
        guestbookEntries={[]}
        moments={[]}
        visitorCollage={collage}
        ambient={{
          aircraft: null,
          birds: null,
          birdCollageUrl: "",
          satellite: null,
          printer: null,
          food: null,
          scoreboard: null,
        }}
        onReturnAmbient={() => {}}
      />,
    );
  }

  it("renders the year-end collage in the focused channel", () => {
    renderFocus(COLLAGE);
    expect(screen.getByRole("heading", { name: "Who's Stopped By" })).toBeTruthy();
    expect(within(grid()).getByText("Alex")).toBeTruthy();
    expect(document.querySelector(".visitor-collage--focus")).toBeTruthy();
  });

  it("renders the empty state in the focused channel", () => {
    renderFocus(null);
    expect(screen.getByText(/No check-ins yet/)).toBeTruthy();
  });
});
