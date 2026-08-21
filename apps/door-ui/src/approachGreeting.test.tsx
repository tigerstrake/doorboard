// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { ApproachGreeting, shouldGreetOnApproach } from "./ApproachGreeting";
import type { SessionState } from "@doorboard/ui-kit";

/**
 * The approach greeting (ADR-0018).
 *
 * The regression this guards: `APPROACH_DETECTED` used not to be a wallboard
 * state, so recognition lit the ESP32 and left the screen silent — the greeting
 * only appeared after somebody rang.
 */

afterEach(cleanup);

describe("shouldGreetOnApproach", () => {
  it("greets on the states recognition reaches without a bell press", () => {
    expect(shouldGreetOnApproach("APPROACH_DETECTED", "Tiger")).toBe(true);
    expect(shouldGreetOnApproach("IDENTITY_CACHED", "Tiger")).toBe(true);
  });

  it("stays silent when nobody is recognised", () => {
    expect(shouldGreetOnApproach("APPROACH_DETECTED", null)).toBe(false);
    expect(shouldGreetOnApproach("IDENTITY_CACHED", "")).toBe(false);
  });

  it("stays silent at idle and once a session is under way", () => {
    // A ring hands over to the visitor-mode takeover, which greets on its own —
    // two greetings at once would be a bug.
    const notApproach: SessionState[] = [
      "IDLE",
      "BUTTON_PRESSED",
      "VISITOR_MODE",
      "RINGING",
      "ANSWERED",
      "UNANSWERED_TIMEOUT",
      "SESSION_END",
    ];
    for (const state of notApproach) {
      expect(shouldGreetOnApproach(state, "Tiger"), state).toBe(false);
    }
  });
});

describe("ApproachGreeting", () => {
  it("renders the name", () => {
    render(
      <ApproachGreeting sessionState="APPROACH_DETECTED" displayName="Tiger" profileId="warm_amber" />
    );
    expect(screen.getByTestId("approach-greeting")).toBeTruthy();
    expect(screen.getByText("Hi Tiger")).toBeTruthy();
  });

  it("renders nothing at all when there is no recognised name", () => {
    const { container } = render(
      <ApproachGreeting sessionState="APPROACH_DETECTED" displayName={null} profileId={null} />
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders a hostile display name inertly", () => {
    const hostile = "<script>alert('xss')</script>";
    render(
      <ApproachGreeting sessionState="IDENTITY_CACHED" displayName={hostile} profileId={null} />
    );
    expect(screen.getByText(`Hi ${hostile}`)).toBeTruthy();
    expect(document.querySelector("script")).toBeNull();
  });

  it("cannot swallow a touch meant for the wallboard beneath it", () => {
    render(
      <ApproachGreeting sessionState="APPROACH_DETECTED" displayName="Tiger" profileId="warm_amber" />
    );
    // The class carries pointer-events: none; assert the hook is present so the
    // CSS contract is not silently renamed away.
    expect(screen.getByTestId("approach-greeting").className).toContain("db-approach-greeting");
  });
});
