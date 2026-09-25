import React, { useEffect, useRef, useState } from "react";
import {
  Tile,
  StatusBadge,
  GreetingBanner,
  RingStatus,
  VideoMessageStatus,
  PollPrompt,
  SessionEndBanner,
  QRPlaceholder,
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
  /**
   * One row per configured presence subject, in configured order, already mapped by
   * subject_id and with `until` honoured — the same rows the ambient Presence tile shows
   * (ADR-0048), so the two tiles cannot disagree about who is which.
   */
  presence: ReadonlyArray<{ subjectId: string; name: string; label: PresenceLabel }>;
  pollQuestion: string;
  visitorUrl: string | null;
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
//
// Display only. The wallboard is a corridor screen nobody touches, so it must never
// drive the session machine: it has no "Done" button and no inactivity countdown. The
// session ends when the DoorPad or door-api says so (SESSION_END/IDLE over /ws) — an
// inactivity timer here used to POST /doorpad/session/end 30 s after every bell, cutting
// off video messages and guestbook notes in progress on the DoorPad.
export function WallboardVisitorMode({
  sessionState,
  sessionId,
  profileId,
  displayName,
  presence,
  pollQuestion,
  visitorUrl,
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
    : "The DoorPad below shows your live self-view and next steps.";

  return (
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
              <li>Wait for someone to come to the door</li>
              <li>Leave a short video message</li>
              <li>Sign our digital guestbook</li>
              <li>Answer the roommate poll</li>
            </ul>
          </div>
        </Tile>

        <Tile title="Availability State">
          <div className="status-display-box">
            {presence.map((row) => (
              <div className="status-display-row" key={row.subjectId} data-subject={row.subjectId}>
                <span className="person-name">{row.name}</span>
                <StatusBadge label={row.label} />
              </div>
            ))}
            {presence.length === 0 && <p>Availability unknown.</p>}
          </div>
        </Tile>

        <Tile title="Scan Visitor QR">
          {visitorUrl ? <QRPlaceholder url={visitorUrl} /> : <p>Preparing visitor link...</p>}
        </Tile>
      </div>

      <PollPrompt question={pollQuestion} />
    </div>
  );
}
