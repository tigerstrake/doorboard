import { describe, expect, it } from "vitest";

import {
  DOORPAD_IDLE_TIMEOUT_MS,
  VISITOR_WRITING_TIMEOUT_MS,
  doorpadResetTimeoutMs,
} from "./doorpadTimeouts";

/**
 * The DoorPad's inactivity budget.
 *
 * This exists because a 30-second countdown ran while the "scan to leave a message"
 * QR was on screen. Its reset calls /doorpad/session/end, which clears the session
 * id — and a visitor token is only honoured while its session is the current one.
 * So the doorboard invalidated the link it had just asked the visitor to scan,
 * about the time they finished their first sentence. Measured on the live door: the
 * visitor endpoint answered 200 at 24s and 401 at 36s.
 *
 * A regression here is invisible in review and silent in production — the visitor
 * simply loses what they typed, on their phone, with nothing on the doorboard to
 * suggest why.
 */
describe("doorpadResetTimeoutMs", () => {
  it("gives a visitor writing on their phone ten minutes", () => {
    expect(doorpadResetTimeoutMs("message", "qr")).toBe(VISITOR_WRITING_TIMEOUT_MS);
    expect(VISITOR_WRITING_TIMEOUT_MS).toBe(600_000);
  });

  it("keeps the short reset for every other screen", () => {
    for (const [screen, step] of [
      ["ringing", "offer"],
      ["message", "offer"],
      ["message", "recording"],
      ["message", "review"],
      ["photo", "offer"],
      ["idle", "offer"],
    ] as const) {
      expect(doorpadResetTimeoutMs(screen, step)).toBe(DOORPAD_IDLE_TIMEOUT_MS);
    }
  });

  it("is not shorter than door-api's visitor token TTL", () => {
    // Three limits govern this link: this timer, visitor_token_ttl_s and
    // inactivity_timeout_s. The shortest is the one a visitor feels, so they are kept
    // equal. If this timer were the shortest, the doorboard would again revoke a
    // link the token still considered valid.
    const VISITOR_TOKEN_TTL_MS = 600_000;
    expect(VISITOR_WRITING_TIMEOUT_MS).toBeGreaterThanOrEqual(VISITOR_TOKEN_TTL_MS);
  });

  it("still resets an abandoned doorboard promptly", () => {
    // The counterweight: someone who walks away mid-interaction must not leave the
    // door showing their session for ten minutes.
    expect(DOORPAD_IDLE_TIMEOUT_MS).toBe(30_000);
  });
});
