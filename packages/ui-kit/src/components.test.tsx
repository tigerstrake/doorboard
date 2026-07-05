// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, cleanup } from "@testing-library/react";
import { Tile } from "./Tile";
import { GreetingBanner } from "./GreetingBanner";
import { StatusBadge } from "./StatusBadge";
import { CountdownAutoReset } from "./CountdownAutoReset";

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
