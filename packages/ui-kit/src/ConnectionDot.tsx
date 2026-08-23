import React from "react";

export type ConnectionLiveness = "live" | "reconnecting" | "stale";

export interface ConnectionDotProps {
  liveness: ConnectionLiveness;
  className?: string;
}

// A small corner dot so a frozen or reconnecting wallboard is visible to a passerby (and to
// the owner). Green = live, amber = reconnecting/stale. Deliberately tiny and calm: the
// wallboard is an ambient display, not an alarm.
const LABELS: Record<ConnectionLiveness, string> = {
  live: "Live",
  reconnecting: "Reconnecting…",
  stale: "Reconnecting…",
};

export function ConnectionDot({ liveness, className = "" }: ConnectionDotProps) {
  const label = LABELS[liveness];
  return (
    <span
      className={`db-connection-dot db-connection-dot--${liveness} ${className}`.trim()}
      data-testid="connection-dot"
      data-liveness={liveness}
      role="status"
      aria-label={`Display connection: ${label}`}
      title={label}
    >
      <span className="db-connection-dot__mark" aria-hidden="true" />
      <span className="db-connection-dot__label">{label}</span>
    </span>
  );
}
