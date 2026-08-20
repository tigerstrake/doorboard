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
 * Ten minutes is a cap. Note that it is currently also the *only* limit: submitting
 * a note is a social write and triggers no session transition, so the session runs
 * to this cap rather than ending when the visitor is done. Ending early on submit
 * would need door-api to end the session when a guestbook entry lands against it.
 * It matches door-api's visitor_token_ttl_s and inactivity_timeout_s,
 * both 600s: three separate limits govern this link, and the shortest of them is the
 * one a visitor actually feels, so they are kept equal on purpose.
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
