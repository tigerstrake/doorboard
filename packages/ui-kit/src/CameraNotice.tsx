import React, { useState } from "react";

export interface CameraNoticeProps {
  /**
   * Which public surface this renders on. The wallboard faces a corridor and is not
   * touchable, so it shows the short line as a static footer. The doorpad is a
   * touchscreen, so it adds a tap-to-expand affordance for the fuller explanation.
   */
  surface: "wallboard" | "doorpad";
  className?: string;
}

// One short line, always visible, and the fuller explanation behind a tap on the doorpad.
// Wording is checked against ARCHITECTURE.md §9 and docs/handoff §2.3: a camera is active;
// recognition is opt-in and personalization-only (never access control); people who are not
// enrolled are not identified and nothing about them is stored; video is recorded only when
// a visitor chooses to leave a message. It must not overstate privacy — visitor-initiated
// video messages ARE recorded, so the notice says exactly that.
const SHORT_NOTICE =
  "Camera in use. Face greetings are opt-in — if you're not enrolled you're not identified or stored. Video records only if you start a message.";

const DETAIL_LINES = [
  "This door has a camera.",
  "Face recognition is opt-in and only greets residents who chose to enroll. It personalizes a greeting — it never controls access.",
  "If you're not enrolled, you're not identified, and nothing about you is stored.",
  "A video message is recorded only when you choose to leave one.",
];

export function CameraNotice({ surface, className = "" }: CameraNoticeProps) {
  const [expanded, setExpanded] = useState(false);
  const canExpand = surface === "doorpad";

  return (
    <aside
      className={`db-camera-notice db-camera-notice--${surface} ${className}`.trim()}
      role="note"
      aria-label="Camera and privacy notice"
    >
      <span className="db-camera-notice__icon" aria-hidden="true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M23 7l-7 5 7 5V7z" />
          <rect x="1" y="5" width="15" height="14" rx="2" ry="2" />
        </svg>
      </span>
      <span className="db-camera-notice__text">{SHORT_NOTICE}</span>
      {canExpand && (
        <button
          type="button"
          className="db-camera-notice__toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? "Hide" : "Details"}
        </button>
      )}
      {canExpand && expanded && (
        <div className="db-camera-notice__detail">
          {DETAIL_LINES.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
      )}
    </aside>
  );
}
