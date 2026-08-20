import React from "react";
import { GreetingBanner, SessionState } from "@doorboard/ui-kit";

/**
 * "Hi Tiger" the moment recognition lands, before anyone touches anything
 * (ADR-0018 §"Decision").
 *
 * Before this existed, `APPROACH_DETECTED` was not a wallboard state: a recognised
 * person got the ESP32 light animation and a completely silent screen, and the
 * greeting only appeared once somebody actually rang.
 *
 * Deliberately an **overlay, not a takeover**. Someone walking down the hallway
 * should be greeted without the ambient wallboard being replaced — a takeover
 * would make the big screen flicker between ambient content and a greeting all
 * day. It also carries `pointer-events: none` so it can never swallow a touch
 * meant for a tile underneath.
 *
 * Extracted from App so the predicate is unit-testable without standing up the
 * whole wallboard, and so the doorpad can reuse it unchanged.
 */

/** States reached by recognition alone, with no bell press. */
export const APPROACH_STATES: SessionState[] = ["APPROACH_DETECTED", "IDENTITY_CACHED"];

/**
 * States where the visitor is mid-interaction, so a "Hi <name>" banner would be in the way.
 *
 * Everything else — including plain IDLE — is fair game *if* the door still knows who is
 * there, which is the point of the change below.
 */
const INTERACTION_STATES: SessionState[] = [
  "BUTTON_PRESSED",
  "VISITOR_MODE",
  "RINGING",
  "ANSWERED",
  "UNANSWERED_TIMEOUT",
  "VIDEO_MESSAGE_OFFERED",
  "VIDEO_MESSAGE_RECORDING",
  "VIDEO_MESSAGE_REVIEW",
  "VIDEO_MESSAGE_SAVED",
  // A finished visit is not a greeting opportunity; the next person starts a new one.
  "SESSION_END",
];

export interface ApproachGreetingProps {
  sessionState: SessionState;
  displayName: string | null;
  profileId: string | null;
}

/**
 * True when a recognised person is at the door and has not rung.
 *
 * Follows the *identity*, not the session. Gating on APPROACH_DETECTED/IDENTITY_CACHED meant
 * the greeting lived exactly as long as door-visiond's 2.5 s identity cache: the moment the
 * visitor looked down at the panel they were standing at, their face left the frame,
 * `vision.identity_expired` dropped the session to IDLE, and "Hi Tiger" vanished — while
 * door-api went on knowing exactly who they were for another 33 seconds (ADR-0020, ADR-0028).
 * The door said "I know you" for two and a half seconds and then looked blank while still
 * knowing. Reported as "it said hi tiger and immediately logged out again".
 *
 * So: greet whenever a name is held and the visitor is not mid-interaction. The name is
 * already the thing that expires on its own schedule, and it is the same value the identity
 * badge and named check-in read — so all three appear and disappear together instead of
 * disagreeing about whether the door knows you.
 */
export function shouldGreetOnApproach(
  sessionState: SessionState,
  displayName: string | null
): boolean {
  return Boolean(displayName) && !INTERACTION_STATES.includes(sessionState);
}

export function ApproachGreeting({
  sessionState,
  displayName,
  profileId,
}: ApproachGreetingProps) {
  if (!shouldGreetOnApproach(sessionState, displayName)) return null;

  return (
    <div className="db-approach-greeting" data-testid="approach-greeting">
      <GreetingBanner
        title={`Hi ${displayName}`}
        subtitle="Good to see you"
        profileId={profileId}
        celebratory
      />
    </div>
  );
}
