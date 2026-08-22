import React, { useEffect, useRef, useState } from "react";
import { CrossfadeSwitch } from "@doorboard/ui-kit";

import { GuestbookQuote } from "../SocialRenderers";

export interface SlideshowEntry {
  id: string;
  text: string;
  author_label?: string | null;
  attributed_to?: string | null;
}

export interface GuestbookSlideshowProps {
  entries: SlideshowEntry[];
  /**
   * Long enough to read a 280-character note without hurrying, which is the whole
   * point — a note that cycles before it can be read is worse than a list.
   */
  intervalMs?: number;
}

export const DEFAULT_ROTATION_MS = 12000;

/**
 * Cycles approved guestbook notes one at a time.
 *
 * The tile used to stack every approved note in a list, where they were collected,
 * moderated, and then not really read. Showing one at a time, large, is what turns
 * the door from a status board into something people write on because they will
 * see it displayed.
 *
 * Two behaviours matter more than the animation:
 *
 *   * A refresh must not restart the rotation. Approved notes are re-fetched
 *     periodically, and resetting to the first note on every poll would mean a
 *     long note is never on screen long enough to finish.
 *   * A note that disappears — deleted by its author, or unapproved — must not
 *     stay on screen. If the current note vanishes from the list, move on rather
 *     than holding a note the household has withdrawn.
 */
export function GuestbookSlideshow({
  entries,
  intervalMs = DEFAULT_ROTATION_MS,
}: GuestbookSlideshowProps) {
  // Tracked by id, not index: an index survives a refresh but points at a
  // different note once anything is inserted or removed.
  const [currentId, setCurrentId] = useState<string | null>(entries[0]?.id ?? null);
  const entriesRef = useRef(entries);
  entriesRef.current = entries;

  // Keep the current note if it is still approved; otherwise fall back to the first.
  useEffect(() => {
    if (entries.length === 0) {
      if (currentId !== null) setCurrentId(null);
      return;
    }
    if (currentId === null || !entries.some((entry) => entry.id === currentId)) {
      setCurrentId(entries[0].id);
    }
  }, [entries, currentId]);

  // One note needs no timer at all — and starting one would re-fire the crossfade
  // onto the same key, which reads as a flicker.
  useEffect(() => {
    if (entries.length < 2) return undefined;
    const timer = window.setInterval(() => {
      const list = entriesRef.current;
      if (list.length < 2) return;
      setCurrentId((id) => {
        const at = list.findIndex((entry) => entry.id === id);
        const next = at === -1 ? 0 : (at + 1) % list.length;
        return list[next].id;
      });
    }, intervalMs);
    return () => window.clearInterval(timer);
    // Deliberately keyed on the COUNT, not the array: a refresh returning the same
    // notes must not reset the interval and cut the current note short.
  }, [entries.length, intervalMs]);

  if (entries.length === 0) {
    // An invitation, not a blank tile. This is the state most visitors see first.
    return (
      <div className="guestbook-slideshow guestbook-slideshow--empty">
        <p className="guestbook-slideshow__invite">No notes yet — leave the first one.</p>
      </div>
    );
  }

  const current = entries.find((entry) => entry.id === currentId) ?? entries[0];
  const position = entries.findIndex((entry) => entry.id === current.id) + 1;

  return (
    <div className="guestbook-slideshow" data-testid="guestbook-slideshow">
      <CrossfadeSwitch activeKey={current.id} className="guestbook-slideshow__stage">
        <GuestbookQuote
          text={current.text}
          authorLabel={current.attributed_to ?? current.author_label ?? null}
        />
      </CrossfadeSwitch>
      {entries.length > 1 && (
        <p className="guestbook-slideshow__position" aria-live="off">
          {position} / {entries.length}
        </p>
      )}
    </div>
  );
}
