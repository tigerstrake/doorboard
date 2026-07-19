// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { AmbientBirdSummaryPayload } from "@doorboard/contracts";
import { WallboardFocusedView } from "./wallboardChannels";

afterEach(() => cleanup());

const BIRDS: AmbientBirdSummaryPayload = {
  window: "today",
  total_detections: 7,
  top_species: [
    { name: "House Finch", count: 4, confidence_avg: 0.88 },
    { name: "Mourning Dove", count: 2, confidence_avg: 0.79 },
  ],
};

function renderBirds(over: { birds?: AmbientBirdSummaryPayload | null; birdCollageUrl?: string }) {
  return render(
    <WallboardFocusedView
      channel="birds"
      poll={null}
      pollResults={null}
      guestbookEntries={[]}
      moments={[]}
      ambient={{
        aircraft: null,
        birds: over.birds === undefined ? BIRDS : over.birds,
        birdCollageUrl: over.birdCollageUrl ?? "",
        satellite: null,
        printer: null,
        food: null,
        scoreboard: null,
      }}
      onReturnAmbient={() => {}}
    />,
  );
}

describe("wallboard birds focus view", () => {
  it("renders species stats above (name + total)", () => {
    renderBirds({});
    expect(screen.getByText("House Finch")).toBeTruthy();
    expect(screen.getByText("Mourning Dove")).toBeTruthy();
    expect(screen.getByText("7")).toBeTruthy(); // total detections hero stat
  });

  it("embeds the collage image below the stats when a URL is configured", () => {
    renderBirds({ birdCollageUrl: "http://bird-pi.local:8080/frame.png" });
    const img = document.querySelector("img.bird-collage") as HTMLImageElement | null;
    expect(img).toBeTruthy();
    expect(img?.getAttribute("src")).toContain("frame.png");
  });

  it("shows no collage element when the URL is unset", () => {
    renderBirds({ birdCollageUrl: "" });
    expect(document.querySelector("img.bird-collage")).toBeNull();
  });

  it("still shows the collage when the stats feed is unavailable", () => {
    renderBirds({ birds: null, birdCollageUrl: "http://bird-pi.local/frame.png" });
    expect(screen.getByText(/unavailable/i)).toBeTruthy();
    expect(document.querySelector("img.bird-collage")).toBeTruthy();
  });
});
