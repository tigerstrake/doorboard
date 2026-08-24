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

  it("keeps greeting at IDLE while the door still knows who is there", () => {
    // The reported bug: "it said hi tiger and immediately logged out again".
    //
    // door-visiond's identity cache is 2.5 s, so the moment someone looks down at the panel
    // they are standing at, their face leaves the frame and the session drops to IDLE — while
    // door-api goes on holding the name for another 33 s (ADR-0020, ADR-0028). Gating the
    // greeting on the *session* made it last two and a half seconds; gating it on the name
    // makes it last as long as the door actually knows you.
    expect(shouldGreetOnApproach("IDLE", "Tiger")).toBe(true);
    // And still nothing when the name has genuinely gone.
    expect(shouldGreetOnApproach("IDLE", null)).toBe(false);
  });

  it("stays silent when nobody is recognised", () => {
    expect(shouldGreetOnApproach("APPROACH_DETECTED", null)).toBe(false);
    expect(shouldGreetOnApproach("IDENTITY_CACHED", "")).toBe(false);
  });

  it("stays silent once a session is under way", () => {
    // A ring hands over to the visitor-mode takeover, which greets on its own —
    // two greetings at once would be a bug. A finished visit is not a greeting either.
    const notApproach: SessionState[] = [
      "BUTTON_PRESSED",
      "VISITOR_MODE",
      "RINGING",
      "ANSWERED",
      "UNANSWERED_TIMEOUT",
      "VIDEO_MESSAGE_OFFERED",
      "VIDEO_MESSAGE_RECORDING",
      "VIDEO_MESSAGE_REVIEW",
      "VIDEO_MESSAGE_SAVED",
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
      <ApproachGreeting sessionState="APPROACH_DETECTED" displayName="Tiger" profileId="sunrise" />
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
      <ApproachGreeting sessionState="APPROACH_DETECTED" displayName="Tiger" profileId="sunrise" />
    );
    // The class carries pointer-events: none; assert the hook is present so the
    // CSS contract is not silently renamed away.
    expect(screen.getByTestId("approach-greeting").className).toContain("db-approach-greeting");
  });
});
