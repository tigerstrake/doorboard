import React from "react";

export type RingState = "RINGING" | "ANSWERED" | "UNANSWERED_TIMEOUT";

export interface RingStatusProps {
  state: RingState;
  className?: string;
}

const copy: Record<RingState, { icon: string; title: string; subtitle: string }> = {
  RINGING: {
    icon: "🔔",
    title: "Ringing…",
    subtitle: "Waiting for an answer.",
  },
  ANSWERED: {
    icon: "✅",
    title: "Answered",
    subtitle: "Someone is on their way, or already responded.",
  },
  UNANSWERED_TIMEOUT: {
    icon: "⏱️",
    title: "No answer",
    subtitle: "Nobody answered in time. You can leave a video message below.",
  },
};

export function RingStatus({ state, className = "" }: RingStatusProps) {
  const { icon, title, subtitle } = copy[state];

  return (
    <div
      className={`db-ring-status db-ring-status--${state.toLowerCase()} ${className}`}
      data-testid="ring-status"
      data-state={state}
    >
      <span className="db-ring-status__icon" aria-hidden="true">
        {icon}
      </span>
      <div className="db-ring-status__text">
        <h2 className="db-ring-status__title">{title}</h2>
        <p className="db-ring-status__subtitle">{subtitle}</p>
      </div>
    </div>
  );
}
