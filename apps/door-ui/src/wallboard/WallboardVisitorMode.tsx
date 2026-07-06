import React, { useEffect, useRef, useState } from "react";
import {
  Tile,
  StatusBadge,
  BigButton,
  GreetingBanner,
  RingStatus,
  VideoMessageStatus,
  PollPrompt,
  SessionEndBanner,
  QRPlaceholder,
  CountdownAutoReset,
} from "@doorboard/ui-kit";
import type { PresenceLabel, SessionState } from "@doorboard/ui-kit";

export interface WallboardVisitorModeProps {
  sessionState: SessionState;
  /** Identifies the current session — used only to detect a brand-new session vs. an in-place update. */
  sessionId: string;
  /** Opaque profile id for an enrolled + consented identity, or null for a generic/unknown visitor. */
  profileId: string | null;
  /** Display name from vision.identity_stable — only ever set for enrolled identities. */
  displayName: string | null;
  presence: {
    owner: { label: PresenceLabel };
    roommate: { label: PresenceLabel };
  };
  pollQuestion: string;
  visitorUrl: string;
  autoResetMs?: number;
  onDone: () => void;
}

function renderStatusPanel(state: SessionState) {
  switch (state) {
    case "RINGING":
    case "ANSWERED":
    case "UNANSWERED_TIMEOUT":
      return <RingStatus state={state} />;
    case "VIDEO_MESSAGE_OFFERED":
    case "VIDEO_MESSAGE_RECORDING":
    case "VIDEO_MESSAGE_REVIEW":
    case "VIDEO_MESSAGE_SAVED":
      return <VideoMessageStatus state={state} />;
    default:
      return null;
  }
}

// Renders every VISITOR_MODE..SESSION_END state. Stays mounted for the whole visitor
// session (the parent keys on ambient-vs-visitor, not on individual states) so a
// late-arriving identity can upgrade the greeting in place without remounting —
// see ARCHITECTURE.md §5 (late recognition never resets the interaction).
export function WallboardVisitorMode({
  sessionState,
  sessionId,
  profileId,
  displayName,
  presence,
  pollQuestion,
  visitorUrl,
  autoResetMs = 30000,
  onDone,
}: WallboardVisitorModeProps) {
  const [justUpgraded, setJustUpgraded] = useState(false);
  const prevSessionIdRef = useRef<string>(sessionId);
  const prevProfileIdRef = useRef<string | null>(profileId);

  useEffect(() => {
    const isNewSession = prevSessionIdRef.current !== sessionId;
    prevSessionIdRef.current = sessionId;

    const upgraded = !isNewSession && profileId !== null && profileId !== prevProfileIdRef.current;
    prevProfileIdRef.current = profileId;

    if (!upgraded) return;

    setJustUpgraded(true);
    const timeout = setTimeout(() => setJustUpgraded(false), 1500);
    return () => clearTimeout(timeout);
  }, [profileId, sessionId]);

  if (sessionState === "SESSION_END") {
    return (
      <div
        className="wallboard-visitor-view wallboard-visitor-view--ending db-app-theme"
        data-testid="wallboard-visitor-mode"
        data-session-state={sessionState}
      >
        <SessionEndBanner />
      </div>
    );
  }

  const isEnrolled = profileId !== null;
  const greetingTitle = displayName ? `Welcome back, ${displayName}!` : "Hello, Visitor!";
  const greetingSubtitle = displayName
    ? "You've been recognized — thanks for stopping by."
    : "Press the bell or leave a video message on the DoorPad.";

  return (
    <CountdownAutoReset onReset={onDone} timeoutMs={autoResetMs}>
      <div
        className="wallboard-visitor-view db-app-theme"
        data-testid="wallboard-visitor-mode"
        data-session-state={sessionState}
      >
        <GreetingBanner
          title={greetingTitle}
          subtitle={greetingSubtitle}
          profileId={profileId}
          celebratory={isEnrolled}
          justUpgraded={justUpgraded}
        />

        {renderStatusPanel(sessionState)}

        <div className="visitor-grid">
          <Tile title="DoorPad Instructions">
            <div className="instructions-card">
              <p>
                Touch the <strong>7" DoorPad screen</strong> below to:
              </p>
              <ul>
                <li>Ring the doorbell again</li>
                <li>Leave a short video message</li>
                <li>Sign our digital guestbook</li>
                <li>Answer the roommate poll</li>
              </ul>
            </div>
          </Tile>

          <Tile title="Availability State">
            <div className="status-display-box">
              <div className="status-display-row">
                <span className="person-name">Taylor (Owner)</span>
                <StatusBadge label={presence.owner.label} />
              </div>
              <div className="status-display-row">
                <span className="person-name">Alex (Roommate)</span>
                <StatusBadge label={presence.roommate.label} />
              </div>
            </div>
          </Tile>

          <Tile title="Scan Visitor QR">
            <QRPlaceholder url={visitorUrl} />
          </Tile>
        </div>

        <PollPrompt question={pollQuestion} />

        <div className="visitor-mode-footer">
          <BigButton variant="primary" onClick={onDone}>
            Done / End Session
          </BigButton>
        </div>
      </div>
    </CountdownAutoReset>
  );
}
