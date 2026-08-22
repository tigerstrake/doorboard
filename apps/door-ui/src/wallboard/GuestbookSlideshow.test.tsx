// @vitest-environment jsdom
import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GuestbookSlideshow, type SlideshowEntry } from "./GuestbookSlideshow";

const NOTES: SlideshowEntry[] = [
  { id: "a", text: "Sorry I missed you", author_label: "Sam" },
  { id: "b", text: "Nice door", author_label: null },
  { id: "c", text: "Call me later", author_label: "Jo" },
];

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("GuestbookSlideshow", () => {
  it("shows one note at a time, not the whole list", () => {
    render(<GuestbookSlideshow entries={NOTES} />);
    expect(screen.getByText(/Sorry I missed you/)).toBeTruthy();
    expect(screen.queryByText(/Call me later/)).toBeNull();
  });

  it("advances to the next note", async () => {
    render(<GuestbookSlideshow entries={NOTES} intervalMs={5000} />);
    expect(screen.getByText(/Sorry I missed you/)).toBeTruthy();
    await tick(5100);
    expect(screen.getByText(/Nice door/)).toBeTruthy();
  });

  it("wraps around at the end", async () => {
    render(<GuestbookSlideshow entries={NOTES} intervalMs={1000} />);
    await tick(3300);
    expect(screen.getByText(/Sorry I missed you/)).toBeTruthy();
  });

  it("invites a first note rather than showing a blank tile", () => {
    render(<GuestbookSlideshow entries={[]} />);
    expect(screen.getByText(/leave the first one/i)).toBeTruthy();
  });

  it("does not rotate or show a counter for a single note", async () => {
    render(<GuestbookSlideshow entries={[NOTES[0]]} intervalMs={1000} />);
    expect(screen.queryByText("1 / 1")).toBeNull();
    await tick(5000);
    // Still the same note, and no crossfade was re-fired onto the same key —
    // which would read as a flicker on the wallboard.
    expect(screen.getByText(/Sorry I missed you/)).toBeTruthy();
  });

  it("keeps its place when a refresh returns the same notes", async () => {
    const { rerender } = render(<GuestbookSlideshow entries={NOTES} intervalMs={5000} />);
    await tick(5100);
    expect(screen.getByText(/Nice door/)).toBeTruthy();

    // A poll returns an equal-but-new array, as a fetch would.
    rerender(<GuestbookSlideshow entries={[...NOTES]} intervalMs={5000} />);
    // Must NOT jump back to the first note: resetting on every refresh means a
    // long note is never on screen long enough to read.
    expect(screen.getByText(/Nice door/)).toBeTruthy();
  });

  it("does not cut a note short when a refresh arrives mid-display", async () => {
    const { rerender } = render(<GuestbookSlideshow entries={NOTES} intervalMs={5000} />);
    await tick(4000);
    rerender(<GuestbookSlideshow entries={[...NOTES]} intervalMs={5000} />);
    await tick(500);
    // 4.5s in: still the first note. A reset interval would have restarted the
    // full 5s here, which is subtler and worse than jumping.
    expect(screen.getByText(/Sorry I missed you/)).toBeTruthy();
    await tick(700);
    expect(screen.getByText(/Nice door/)).toBeTruthy();
  });

  it("drops a note that stops being approved", async () => {
    const { rerender } = render(<GuestbookSlideshow entries={NOTES} intervalMs={5000} />);
    await tick(5100);
    expect(screen.getByText(/Nice door/)).toBeTruthy();

    // The author asked for it to be deleted, or the owner unapproved it.
    rerender(
      <GuestbookSlideshow entries={NOTES.filter((n) => n.id !== "b")} intervalMs={5000} />
    );
    expect(screen.queryByText(/Nice door/)).toBeNull();
  });

  it("handles every note disappearing", () => {
    const { rerender } = render(<GuestbookSlideshow entries={NOTES} />);
    rerender(<GuestbookSlideshow entries={[]} />);
    expect(screen.getByText(/leave the first one/i)).toBeTruthy();
  });

  it("prefers a recognised name over the typed label", () => {
    // ADR-0018 E-24: door-api only sets attributed_to when public attribution is
    // on, so trusting it here cannot leak a name the household kept private.
    render(
      <GuestbookSlideshow
        entries={[{ id: "x", text: "hello", author_label: "typed", attributed_to: "Tiger" }]}
      />
    );
    expect(screen.getByText(/Tiger/)).toBeTruthy();
    expect(screen.queryByText(/typed/)).toBeNull();
  });

  it("shows position so a reader knows the rotation is finite", () => {
    render(<GuestbookSlideshow entries={NOTES} />);
    expect(screen.getByText("1 / 3")).toBeTruthy();
  });
});
