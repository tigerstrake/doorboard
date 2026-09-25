import React from "react";

/**
 * "The door recognises you as Tiger" — shown before anyone writes anything
 * (ADR-0018 E-23).
 *
 * E-23 is a flat requirement: any surface that attaches identity to a write says
 * so **before** the write happens, because someone who did not realise the door
 * knew them must not discover it from a stats page afterwards. When attribution
 * shipped, only the phone relay page honoured that — the on-wifi `/visitor` page
 * and the doorpad attributed silently, which was a defect against a rule written
 * two commits earlier.
 *
 * The name comes from door-api's `attributed_to`, which is null unless somebody is
 * recognised *and* their consent covers attribution. Deliberately not re-derived
 * here: a surface that computed the consent gate itself could drift out of step
 * and start attributing silently again.
 */

export interface AttributionNoticeProps {
  /** door-api's `attributed_to`; null when nothing will be attributed. */
  attributedTo: string | null;
  /** What the visitor is about to do, e.g. "leave" or "write". */
  verb?: string;
  className?: string;
  /**
   * One line instead of two paragraphs, for composers on the 600 px panel where the
   * full notice pushed the Submit button below the fold. Says the same three things:
   * who the door thinks you are, that the write will carry it, and the way out.
   */
  compact?: boolean;
}

export function AttributionNotice({
  attributedTo,
  verb = "leave",
  className = "",
  compact = false,
}: AttributionNoticeProps) {
  if (!attributedTo) return null;

  if (compact) {
    return (
      <div
        className={`db-attribution-notice db-attribution-notice--compact ${className}`}
        data-testid="attribution-notice"
        role="status"
      >
        <p>
          The door recognises you as <strong>{attributedTo}</strong> — your name will be attached
          to anything you {verb} here. Ask the household admin to remove it.
        </p>
      </div>
    );
  }

  return (
    <div
      className={`db-attribution-notice ${className}`}
      data-testid="attribution-notice"
      role="status"
    >
      <p>
        The door recognises you as <strong>{attributedTo}</strong>, so your name will be attached to
        anything you {verb} here.
      </p>
      <p className="db-attribution-notice__aside">
        Ask the household admin to remove it, or to stop recognising you altogether.
      </p>
    </div>
  );
}
