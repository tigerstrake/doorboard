import { describe, expect, it } from "vitest";
import {
  WALLBOARD_FOCUS_TIMEOUT_MS,
  WALLBOARD_FOCUS_WS_TYPE,
  createWallboardFocusRequest,
  wallboardFocusRequestFromMessage,
} from "./wallboardChannelModel";

// Mirror the ephemeral control message door-api's POST /wallboard/focus fans
// out on /ws. The wallboard's event-client hands this straight to
// wallboardFocusRequestFromMessage.
function focusMessage(overrides: Record<string, unknown> = {}) {
  const request = createWallboardFocusRequest("aircraft");
  return { type: WALLBOARD_FOCUS_WS_TYPE, ...request, ...overrides };
}

describe("wallboardFocusRequestFromMessage", () => {
  it("builds a focus request from a valid focus_changed message", () => {
    const request = wallboardFocusRequestFromMessage(focusMessage());
    expect(request).not.toBeNull();
    expect(request?.mode).toBe("focus");
    expect(request?.channel).toBe("aircraft");
    // expiresAt drives the wallboard's 120s auto-return-to-ambient.
    expect(request?.expiresAt).toBe(request!.requestedAt + WALLBOARD_FOCUS_TIMEOUT_MS);
  });

  it("builds an ambient request (null channel, no expiry) from an ambient message", () => {
    const message = { type: WALLBOARD_FOCUS_WS_TYPE, ...createWallboardFocusRequest("ambient") };
    const request = wallboardFocusRequestFromMessage(message);
    expect(request?.mode).toBe("ambient");
    expect(request?.channel).toBeNull();
    expect(request?.expiresAt).toBeNull();
  });

  it("ignores the message's extra `type` discriminator (not stored on the request)", () => {
    const request = wallboardFocusRequestFromMessage(focusMessage());
    expect(request).not.toHaveProperty("type");
  });

  it("returns null for an unknown channel", () => {
    expect(wallboardFocusRequestFromMessage(focusMessage({ channel: "not-a-tile" }))).toBeNull();
  });

  it("returns null when mode is focus but channel is null", () => {
    expect(
      wallboardFocusRequestFromMessage(focusMessage({ mode: "focus", channel: null }))
    ).toBeNull();
  });

  it("returns null for non-object / nullish input", () => {
    expect(wallboardFocusRequestFromMessage(null)).toBeNull();
    expect(wallboardFocusRequestFromMessage("wallboard.focus_changed")).toBeNull();
    expect(wallboardFocusRequestFromMessage(42)).toBeNull();
  });
});
