// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, cleanup } from "@testing-library/react";
import { Tile } from "./Tile";
import { GreetingBanner } from "./GreetingBanner";
import { StatusBadge } from "./StatusBadge";
import { CountdownAutoReset } from "./CountdownAutoReset";
import { PollPrompt } from "./PollPrompt";
import { SessionEndBanner } from "./SessionEndBanner";
import { RingStatus } from "./RingStatus";
import { VideoMessageStatus } from "./VideoMessageStatus";

describe("Component Security and Escaping", () => {
  const dangerousString = "<script>alert(1)</script>";

  afterEach(() => {
    cleanup();
  });

  it("should escape script tags when rendering Tile title", () => {
    render(<Tile title={dangerousString} />);
    const heading = screen.getByRole("heading", { level: 3 });
    expect(heading.textContent).toBe(dangerousString);
    // Verify it is not an actual script tag element
    expect(document.querySelector("script")).toBeNull();
  });

  it("should escape script tags when rendering GreetingBanner title", () => {
    render(<GreetingBanner title={dangerousString} />);
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent).toBe(dangerousString);
    expect(document.querySelector("script")).toBeNull();
  });

  it("should escape script tags when rendering StatusBadge customText", () => {
    render(<StatusBadge label="available" customText={dangerousString} />);
    const badgeText = screen.getByText(dangerousString);
    expect(badgeText.textContent).toBe(dangerousString);
    expect(document.querySelector("script")).toBeNull();
  });

  it("should escape script tags when rendering PollPrompt question", () => {
    render(<PollPrompt question={dangerousString} />);
    const text = screen.getByText(dangerousString);
    expect(text.textContent).toBe(dangerousString);
    expect(document.querySelector("script")).toBeNull();
  });

  it("should escape script tags when rendering SessionEndBanner title", () => {
    render(<SessionEndBanner title={dangerousString} />);
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent).toBe(dangerousString);
    expect(document.querySelector("script")).toBeNull();
  });
});

describe("RingStatus", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders distinct copy per ring state", () => {
    const { rerender } = render(<RingStatus state="RINGING" />);
    expect(screen.getByTestId("ring-status").getAttribute("data-state")).toBe("RINGING");

    rerender(<RingStatus state="ANSWERED" />);
    expect(screen.getByTestId("ring-status").getAttribute("data-state")).toBe("ANSWERED");

    rerender(<RingStatus state="UNANSWERED_TIMEOUT" />);
    expect(screen.getByTestId("ring-status").getAttribute("data-state")).toBe("UNANSWERED_TIMEOUT");
  });
});

describe("VideoMessageStatus", () => {
  afterEach(() => {
    cleanup();
  });

  it("never renders a video/camera element for any state", () => {
    (
      ["VIDEO_MESSAGE_OFFERED", "VIDEO_MESSAGE_RECORDING", "VIDEO_MESSAGE_REVIEW", "VIDEO_MESSAGE_SAVED"] as const
    ).forEach((state) => {
      const { unmount } = render(<VideoMessageStatus state={state} />);
      expect(document.querySelector("video")).toBeNull();
      expect(document.querySelector("img")).toBeNull();
      unmount();
    });
  });
});

describe("CountdownAutoReset", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("should call onReset after the timeout is reached", () => {
    const onResetMock = vi.fn();
    render(
      <CountdownAutoReset onReset={onResetMock} timeoutMs={2000}>
        <div>Test Child</div>
      </CountdownAutoReset>
    );

    // Verify callback hasn't been fired yet
    expect(onResetMock).not.toHaveBeenCalled();

    // Advance timers by less than the timeout (e.g., 1000ms)
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(onResetMock).not.toHaveBeenCalled();

    // Advance remaining time (another 1000ms)
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(onResetMock).toHaveBeenCalledTimes(1);
  });
});
