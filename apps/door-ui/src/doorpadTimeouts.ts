/**
 * How long the DoorPad waits before resetting itself to IDLE.
 *
 * The short value exists for someone who walked away mid-interaction. It must not
 * apply while the "scan to leave a message" QR is on screen: at that point the
 * visitor is typing on their phone, the doorboard has nothing further to show
 * them, and 30 seconds is nowhere near enough to write a note.
 *
 * That mattered more than it looks. The reset calls `/doorpad/session/end`, which
 * ends the session server-side (`trigger=visitor:end`) and clears `session_id` —
 * and the visitor token is only accepted while its session is the current one. So
 * the countdown was invalidating the very link it had just told the visitor to
 * scan, at about the time they finished their first sentence. Measured on the live
 * door: the link answered 200 at 24s and 401 at 36s. It also meant the session
 * never reached UNANSWERED_TIMEOUT, so the server's own message path never opened
 * at all.
 *
 * Ten minutes is a cap, not an expectation — the session ends as soon as the note
 * is submitted. It is deliberately longer than the visitor token's 5-minute TTL so
 * that the token, not this timer, decides how long the link lives.
 */
export const DOORPAD_IDLE_TIMEOUT_MS = 30_000;
export const VISITOR_WRITING_TIMEOUT_MS = 10 * 60_000;

/** The screens the DoorPad can be showing, as far as the reset timer cares. */
export type DoorPadScreen = string;

/**
 * Pick the inactivity budget for the screen currently on the doorboard.
 *
 * Extracted from the JSX so the rule is nameable and testable: a wrong value here
 * is invisible in review and only shows up as a visitor losing their message.
 */
export function doorpadResetTimeoutMs(
  doorPadScreen: DoorPadScreen,
  videoStep: string,
): number {
  const visitorIsWritingOnTheirPhone = doorPadScreen === "message" && videoStep === "qr";
  return visitorIsWritingOnTheirPhone ? VISITOR_WRITING_TIMEOUT_MS : DOORPAD_IDLE_TIMEOUT_MS;
}
