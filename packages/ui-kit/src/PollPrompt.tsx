import React from "react";

export interface PollPromptProps {
  question: string;
  ctaText?: string;
  className?: string;
}

// Read-only prompt only — casting a vote is a DoorPad/UGC concern owned elsewhere.
export function PollPrompt({
  question,
  ctaText = "Vote on the DoorPad",
  className = "",
}: PollPromptProps) {
  return (
    <div className={`db-poll-prompt ${className}`} data-testid="poll-prompt">
      <span className="db-poll-prompt__icon" aria-hidden="true">
        📊
      </span>
      <div className="db-poll-prompt__text">
        <p className="db-poll-prompt__question">{question}</p>
        <p className="db-poll-prompt__cta">{ctaText}</p>
      </div>
    </div>
  );
}
