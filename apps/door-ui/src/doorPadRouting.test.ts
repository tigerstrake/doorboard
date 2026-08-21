// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import type { SessionState } from "@doorboard/contracts";
import { doorPadRouteForState } from "./App";

/**
 * Which session transitions are allowed to move the visitor between screens.
 *
 * The reported bug: "it said hi tiger and immediately logged out again without giving me a
 * chance to send a visitor check in." door-visiond's identity cache is 2.5 s, so the moment
 * someone looks down at the panel they are standing at their face leaves the frame,
 * `vision.identity_expired` drops the session to IDLE, and IDLE routed the doorpad home —
 * yanking anyone who had tapped into Check In, the guestbook or a poll out of it, while
 * door-api still knew exactly who they were for another 33 seconds.
 */

describe("doorPadRouteForState", () => {
  it("does not move the visitor when a session merely lapses to IDLE", () => {
    // null means "this transition implies nothing about where the visitor should be".
    expect(doorPadRouteForState("IDLE").screen).toBeNull();
  });

  it("does not move the visitor on recognition alone", () => {
    // Already true before this fix: walking up must not open a bell-ringing UI for somebody
    // who has not rung (ARCHITECTURE.md §5.4).
    expect(doorPadRouteForState("APPROACH_DETECTED").screen).toBeNull();
    expect(doorPadRouteForState("IDENTITY_CACHED").screen).toBeNull();
  });

  it("still goes home when a visit genuinely finishes", () => {
    // The distinction that makes this safe: a session *ending* is a real reason to reset the
    // panel for the next person. A session lapsing is not.
    expect(doorPadRouteForState("SESSION_END").screen).toBe("home");
  });

  it("still drives the screens a state genuinely implies", () => {
    expect(doorPadRouteForState("VIDEO_MESSAGE_OFFERED")).toEqual({
      screen: "message",
      video: "offer",
    });
    expect(doorPadRouteForState("VIDEO_MESSAGE_RECORDING").video).toBe("recording");
    expect(doorPadRouteForState("VIDEO_MESSAGE_REVIEW").video).toBe("review");
    expect(doorPadRouteForState("VIDEO_MESSAGE_SAVED").video).toBe("saved");
    expect(doorPadRouteForState("RINGING").screen).toBe("ringing");
    expect(doorPadRouteForState("BUTTON_PRESSED").screen).toBe("ringing");
  });

  it("never returns an unknown screen for any state in the contract", () => {
    const states: SessionState[] = [
      "IDLE",
      "APPROACH_DETECTED",
      "IDENTITY_CACHED",
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
    const allowed = new Set([null, "home", "ringing", "message"]);
    for (const state of states) {
      expect(allowed.has(doorPadRouteForState(state).screen), state).toBe(true);
    }
  });
});
