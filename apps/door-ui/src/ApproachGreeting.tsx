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

export interface ApproachGreetingProps {
  sessionState: SessionState;
  displayName: string | null;
  profileId: string | null;
}

/** True when a recognised person is at the door and has not rung. */
export function shouldGreetOnApproach(
  sessionState: SessionState,
  displayName: string | null
): boolean {
  return APPROACH_STATES.includes(sessionState) && Boolean(displayName);
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
