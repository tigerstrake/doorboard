import React from "react";

// Small presentational pieces shared by the DoorPad and the Wallboard
// ambient tiles for rendering user-generated content (T-403). Extracted so
// the "renders inert everywhere" guarantee can be unit-tested directly
// against the exact markup used in production, not a duplicate.
//
// Rule: always render UGC as a JSX text child (React escapes it on the way
// to the DOM) — never via dangerouslySetInnerHTML or string concatenation
// into markup.

export interface GuestbookQuoteProps {
  text: string;
  authorLabel?: string | null;
}

export function GuestbookQuote({ text, authorLabel }: GuestbookQuoteProps) {
  return (
    <blockquote className="guestbook-quote">
      <p>&quot;{text}&quot;</p>
      {authorLabel && <cite>— {authorLabel}</cite>}
    </blockquote>
  );
}

export interface PollOptionRowProps {
  text: string;
  votes: number;
  /** Share of the total cast. Omit to render the count alone, with no bar. */
  pct?: number;
  isLeader?: boolean;
}

export function PollOptionRow({ text, votes, pct, isLeader }: PollOptionRowProps) {
  return (
    <div className={`poll-option-row${isLeader ? " poll-option-row--leader" : ""}`}>
      <div className="poll-option-row__head">
        <span>{text}</span>
        <span className="poll-option-row__count">
          <strong>{votes}</strong> {votes === 1 ? "vote" : "votes"}
          {pct === undefined ? null : ` · ${pct.toFixed(0)}%`}
        </span>
      </div>
      {/*
        The wallboard grid tile is the always-visible poll surface, so it gets the graph too
        — the focus panel and the doorpad already had one. Share of the total cast, matching
        both, because three renderings of one tally must not disagree.
      */}
      {pct === undefined ? null : (
        <div
          className="poll-option-row__bar"
          role="progressbar"
          aria-valuenow={Math.round(pct)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={text}
        >
          <span style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}
