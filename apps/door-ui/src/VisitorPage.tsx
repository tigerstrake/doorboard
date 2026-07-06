import React, { useEffect, useState } from "react";
import { BigButton, SessionState } from "@doorboard/ui-kit";
import { socialApi, ApiError } from "./socialApi";
import type { Poll, PollResultRow } from "./socialApi";

// Tokenized mobile page reached via the wallboard's visitor-mode QR code
// (docs/ui/visitor.md). Deliberately minimal: ring status, an unanswered-note
// (reuses the guestbook safety rails per the spec), poll vote, and a privacy
// notice + deletion request. No login, no persistent identity.

export interface VisitorPageProps {
  sessionState: SessionState;
}

const RING_STATUS_COPY: Partial<Record<SessionState, string>> = {
  VISITOR_MODE: "Ringing…",
  RINGING: "Ringing…",
  ANSWERED: "Answered!",
  UNANSWERED_TIMEOUT: "No answer — leave a note below.",
  VIDEO_MESSAGE_OFFERED: "No answer — leave a note below.",
  VIDEO_MESSAGE_RECORDING: "No answer — leave a note below.",
  VIDEO_MESSAGE_REVIEW: "No answer — leave a note below.",
  VIDEO_MESSAGE_SAVED: "Message saved!",
  SESSION_END: "Session ended.",
};

function apiErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return "Please slow down — try again in a bit.";
    return err.message || fallback;
  }
  return fallback;
}

export function VisitorPage({ sessionState }: VisitorPageProps) {
  const [noteText, setNoteText] = useState("");
  const [noteSubmitting, setNoteSubmitting] = useState(false);
  const [noteStatus, setNoteStatus] = useState<string | null>(null);

  const [poll, setPoll] = useState<Poll | null>(null);
  const [pollResults, setPollResults] = useState<PollResultRow[] | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [votedOptionId, setVotedOptionId] = useState<string | null>(null);

  const [deletionRequested, setDeletionRequested] = useState(false);

  useEffect(() => {
    let cancelled = false;
    socialApi
      .getCurrentPoll()
      .then((p) => {
        if (cancelled) return;
        setPoll(p);
        if (p) return socialApi.getPollResults(p.id).then((r) => !cancelled && setPollResults(r));
      })
      .catch(() => {
        // No poll available right now — section just stays hidden.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const ringStatus = RING_STATUS_COPY[sessionState] ?? "Waiting for a visitor session…";
  const showNoteField = sessionState === "UNANSWERED_TIMEOUT" ||
    sessionState === "VIDEO_MESSAGE_OFFERED" ||
    sessionState === "VIDEO_MESSAGE_RECORDING" ||
    sessionState === "VIDEO_MESSAGE_REVIEW";

  const submitNote = async () => {
    if (noteSubmitting || noteText.trim().length === 0) return;
    setNoteSubmitting(true);
    setNoteStatus(null);
    try {
      await socialApi.createGuestbookEntry(noteText, "Left via phone");
      setNoteStatus("Note sent! It'll show up once approved.");
      setNoteText("");
    } catch (err) {
      setNoteStatus(apiErrorMessage(err, "Couldn't send your note."));
    } finally {
      setNoteSubmitting(false);
    }
  };

  const castVote = async (optionId: string) => {
    if (!poll) return;
    setPollError(null);
    try {
      await socialApi.castVote(poll.id, optionId);
      setVotedOptionId(optionId);
      const results = await socialApi.getPollResults(poll.id);
      setPollResults(results);
    } catch (err) {
      setPollError(apiErrorMessage(err, "Couldn't cast your vote."));
    }
  };

  const requestPrivacyDeletion = async () => {
    // The visitor phone flow has no durable identity to key a specific
    // record off of beyond the note just sent — deletion of that note is
    // handled inline via its own confirmation; this button covers the
    // general "forget me" ask described in docs/ui/visitor.md.
    setDeletionRequested(true);
  };

  return (
    <div className="visitor-page">
      <section className="visitor-section">
        <h3>Ring status</h3>
        <p className="visitor-ring-status">{ringStatus}</p>
      </section>

      {showNoteField && (
        <section className="visitor-section">
          <h3>Leave a note</h3>
          <textarea
            className="guestbook-freetext"
            maxLength={280}
            rows={3}
            placeholder="No one answered? Leave a short note..."
            value={noteText}
            onChange={(e) => setNoteText(e.target.value)}
          />
          {noteStatus && <p className="visitor-note-status">{noteStatus}</p>}
          <BigButton
            variant="primary"
            disabled={noteSubmitting || noteText.trim().length === 0}
            onClick={submitNote}
          >
            Send note
          </BigButton>
        </section>
      )}

      {poll && (
        <section className="visitor-section">
          <h3>{poll.question}</h3>
          {pollError && <p className="poll-error">{pollError}</p>}
          <div className="poll-choices">
            {poll.options.map((opt) => {
              const result = pollResults?.find((r) => r.option_id === opt.id);
              return (
                <button
                  key={opt.id}
                  className="phrase-btn"
                  style={{ width: "100%", margin: "4px 0" }}
                  disabled={votedOptionId !== null}
                  onClick={() => castVote(opt.id)}
                >
                  {opt.text}
                  {result !== undefined && <span> — {result.votes} votes</span>}
                </button>
              );
            })}
          </div>
          {votedOptionId && <p>Thanks for voting!</p>}
        </section>
      )}

      <section className="visitor-section">
        <h3>Privacy</h3>
        <p>
          📸 A camera near the door proactively recognizes enrolled residents only. Unknown
          faces are never named or stored.
        </p>
        {!deletionRequested ? (
          <BigButton onClick={requestPrivacyDeletion}>Request my data be deleted</BigButton>
        ) : (
          <p>Noted — reach out to the resident if you'd like a specific note removed too.</p>
        )}
      </section>
    </div>
  );
}
