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
}

export function PollOptionRow({ text, votes }: PollOptionRowProps) {
  return (
    <div className="poll-option-row">
      <span>{text}</span>
      <span>
        <strong>{votes}</strong> votes
      </span>
    </div>
  );
}
