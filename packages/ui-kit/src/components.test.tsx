// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, cleanup } from "@testing-library/react";
import { Tile } from "./Tile";
import { CrossfadeSwitch } from "./CrossfadeSwitch";
import { GreetingBanner } from "./GreetingBanner";
import { StatusBadge } from "./StatusBadge";
import type { PresenceLabel } from "@doorboard/contracts";
import { CountdownAutoReset } from "./CountdownAutoReset";
import { Gauge } from "./Gauge";
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

  it("should escape script tags when rendering Gauge", () => {
    render(<Gauge title={dangerousString} value="50" />);
    const titleText = screen.getByText(dangerousString);
    expect(titleText.textContent).toBe(dangerousString);
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

  it("does not reset while paused and restarts the timeout after resuming", () => {
    const onResetMock = vi.fn();
    const { rerender } = render(
      <CountdownAutoReset onReset={onResetMock} timeoutMs={2000} paused>
        <div>Recording</div>
      </CountdownAutoReset>
    );

    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(onResetMock).not.toHaveBeenCalled();

    rerender(
      <CountdownAutoReset onReset={onResetMock} timeoutMs={2000} paused={false}>
        <div>Review</div>
      </CountdownAutoReset>
    );
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(onResetMock).toHaveBeenCalledTimes(1);
  });
});

describe("Gauge", () => {
  it("should render value, title, and calculate percentage correctly", () => {
    const { container } = render(
      <Gauge title="SSD Space" value={50} max={100} unit="%" />
    );
    expect(screen.getByText("SSD Space")).toBeTruthy();
    expect(screen.getByText("50")).toBeTruthy();
    expect(screen.getByText("%")).toBeTruthy();
    
    const fillBar = container.querySelector(".gauge-bar-fill");
    expect(fillBar).toBeTruthy();
    expect(fillBar?.getAttribute("style")).toContain("width: 50%");
  });
});

describe("CrossfadeSwitch variants", () => {
  // This file scopes cleanup per describe block rather than globally.
  afterEach(() => {
    cleanup();
  });

  it("keeps the outgoing view mounted so a swap never shows a bare frame", () => {
    const { rerender } = render(
      <CrossfadeSwitch activeKey="ambient">
        <p>ambient grid</p>
      </CrossfadeSwitch>
    );
    rerender(
      <CrossfadeSwitch activeKey="food">
        <p>food focus</p>
      </CrossfadeSwitch>
    );
    // Both present mid-transition: that is the whole point of the component.
    expect(screen.getByText("ambient grid")).toBeTruthy();
    expect(screen.getByText("food focus")).toBeTruthy();
  });

  it("defaults to the plain fade, so existing callers are unchanged", () => {
    render(
      <CrossfadeSwitch activeKey="a">
        <p>a</p>
      </CrossfadeSwitch>
    );
    expect(screen.getByTestId("crossfade-switch").getAttribute("data-variant")).toBe("fade");
  });

  it("marks the zoom variant so opening a channel can read as moving into it", () => {
    render(
      <CrossfadeSwitch activeKey="a" variant="zoom">
        <p>a</p>
      </CrossfadeSwitch>
    );
    const root = screen.getByTestId("crossfade-switch");
    expect(root.getAttribute("data-variant")).toBe("zoom");
    expect(root.className).toContain("db-crossfade--zoom");
  });

  it("publishes its duration to CSS so the fade and the zoom cannot drift apart", () => {
    render(
      <CrossfadeSwitch activeKey="a" variant="zoom" durationMs={250}>
        <p>a</p>
      </CrossfadeSwitch>
    );
    const layer = screen.getByTestId("crossfade-switch").firstElementChild as HTMLElement;
    expect(layer.style.getPropertyValue("--db-crossfade-duration")).toBe("250ms");
    expect(layer.style.transitionDuration).toBe("250ms");
  });
});

describe("StatusBadge presence labels (ADR-0035)", () => {
  // Every label the contract can carry must render. labelDisplayNames and labelIcons are
  // both Record<PresenceLabel, ...>, so a half-added label is a compile error rather than
  // a badge that silently falls back to "Unknown" — this pins the runtime half.
  const allLabels: PresenceLabel[] = [
    "social",
    "available",
    "busy",
    "knock_if_urgent",
    "do_not_disturb",
    "sleeping",
    "at_class",
    "at_library",
    "away",
    "unknown",
  ];

  it.each(allLabels)("renders %s with its own text and class", (label) => {
    const { container } = render(<StatusBadge label={label} />);
    const badge = container.querySelector(`.db-status-badge--${label}`);
    expect(badge).toBeTruthy();
    // Not falling through to the unknown icon/text.
    if (label !== "unknown") {
      expect(badge?.textContent).not.toBe("Unknown");
    }
    expect(badge?.textContent?.trim()).toBeTruthy();
  });

  it("distinguishes the active invitation from passive presence", () => {
    // The whole reason `social` exists: `available` means "I exist", not "come in".
    const { container: social } = render(<StatusBadge label="social" />);
    const { container: available } = render(<StatusBadge label="available" />);
    expect(social.textContent).toBe("Come In");
    expect(available.textContent).toBe("Available");
  });

  it("gives busy and knock_if_urgent different text", () => {
    // `busy` used to carry both meanings ambiguously: don't knock, or knock anyway?
    const { container: busy } = render(<StatusBadge label="busy" />);
    const { container: knock } = render(<StatusBadge label="knock_if_urgent" />);
    expect(busy.textContent).toBe("Busy");
    expect(knock.textContent).toBe("Knock if urgent");
  });

  it("shows do_not_disturb as Locked In", () => {
    // A retext, not a new label — a third don't-bother-me state would read worse.
    const { container } = render(<StatusBadge label="do_not_disturb" />);
    expect(container.textContent).toBe("Locked In");
  });
});
