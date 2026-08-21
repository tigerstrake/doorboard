"use client";

/**
 * The visitor flow (ADR-0017), mirroring door-ui's `VisitorPage` in copy and
 * capability: ring status, leave a note, vote in the poll, ask for deletion.
 *
 * Deliberately minimal, per docs/ui/visitor.md: no login, no persistent identity,
 * nothing about the household. The two implementations are separate because one
 * talks to door-api on the LAN and this one talks to the relay — see ADR-0017's
 * consequences for why that duplication is accepted rather than abstracted.
 *
 * Writes are eventually consistent: they queue at the relay and door-api collects
 * them within a couple of seconds, so the UI reports "sending" and then confirms
 * from the snapshot rather than pretending the write already landed.
 */
import { useCallback, useEffect, useRef, useState } from "react";

const POLL_INTERVAL_MS = 2000;
const NOTE_MAX = 500;

type Access = "checking" | "valid" | "invalid" | "expired";

interface PollOption {
  option_id: string;
  label: string;
}

interface Snapshot {
  session_id: string;
  state: string;
  expires_at: string;
  /** Set when the door recognises whoever is standing there (ADR-0018 §2). */
  attributed_to?: string | null;
  poll: { poll_id: string; question: string; options: PollOption[] } | null;
  poll_results: Array<{ option_id: string; votes: number }> | null;
  outcomes: Array<{
    action_id: string;
    kind: string;
    status: string;
    reason: string | null;
    entry_id: string | null;
  }>;
  pushed_at: string;
}

const RING_STATUS_COPY: Record<string, string> = {
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

/** An outcome that will not change again, so it is safe to remember. */
function isSettled(outcome: { status: string }): boolean {
  return outcome.status === "applied" || outcome.status === "rejected";
}

/** Reasons where offering another attempt would only waste the visitor's time. */
const TERMINAL_VOTE_REASONS = new Set(["poll_closed", "already_voted"]);

export default function VisitorFlow({ token }: { token: string }) {
  const [access, setAccess] = useState<Access>("checking");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  /**
   * Settled outcomes, latched by action id.
   *
   * The snapshot is a live view of the door, and door-api repeats every outcome on
   * each push so a receipt survives. It used not to, and a note that had been
   * delivered reverted to "Sending to the door…" seconds later. Remembering
   * settled outcomes here means one dropped push can no longer unsay something
   * the door already confirmed.
   */
  const [settled, setSettled] = useState<Record<string, Snapshot["outcomes"][number]>>({});

  const [noteText, setNoteText] = useState("");
  const [noteActionId, setNoteActionId] = useState<string | null>(null);
  const [noteSending, setNoteSending] = useState(false);
  const [noteError, setNoteError] = useState<string | null>(null);

  const [selectedOptionId, setSelectedOptionId] = useState<string | null>(null);
  const [voteActionId, setVoteActionId] = useState<string | null>(null);
  const [voteError, setVoteError] = useState<string | null>(null);

  const [deletionActionId, setDeletionActionId] = useState<string | null>(null);
  const [deletionError, setDeletionError] = useState<string | null>(null);

  const encodedToken = useRef(encodeURIComponent(token));

  const refresh = useCallback(async () => {
    try {
      const resp = await fetch(`/api/visitor/${encodedToken.current}`, { cache: "no-store" });
      if (resp.status === 404) {
        setAccess((current) => (current === "valid" ? "expired" : "invalid"));
        return;
      }
      if (!resp.ok) return;
      const body = (await resp.json()) as Snapshot;
      setSnapshot(body);
      setSettled((current) => {
        const arriving = body.outcomes.filter(isSettled).filter((entry) => !current[entry.action_id]);
        if (arriving.length === 0) return current;
        return { ...current, ...Object.fromEntries(arriving.map((entry) => [entry.action_id, entry])) };
      });
      setAccess("valid");
    } catch {
      // Transient network trouble on a phone is normal; keep the last snapshot.
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const submit = useCallback(
    async (body: Record<string, unknown>): Promise<string | null> => {
      const resp = await fetch(`/api/visitor/${encodedToken.current}/action`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const parsed = (await resp.json().catch(() => ({}))) as { error?: string };
        throw new Error(writeErrorMessage(parsed.error, resp.status));
      }
      const accepted = (await resp.json()) as { action_id: string };
      return accepted.action_id;
    },
    [],
  );

  const outcomeFor = (actionId: string | null) =>
    actionId
      ? (settled[actionId] ?? snapshot?.outcomes.find((entry) => entry.action_id === actionId))
      : undefined;

  const noteOutcome = outcomeFor(noteActionId);
  const voteOutcome = outcomeFor(voteActionId);
  const deletionOutcome = outcomeFor(deletionActionId);

  const voteInFlight = Boolean(voteActionId) && voteOutcome === undefined;
  // Retrying is offered for a transient refusal (rate limiting, a door error) but
  // not for one that will refuse again — being told to try again and then refused
  // identically is worse than being told why.
  const voteRetryable =
    !voteInFlight &&
    voteOutcome?.status !== "applied" &&
    !TERMINAL_VOTE_REASONS.has(voteOutcome?.reason ?? "");

  const sendNote = async () => {
    const text = noteText.trim();
    if (text.length === 0) {
      setNoteError("Please write something first.");
      return;
    }
    setNoteError(null);
    setNoteSending(true);
    try {
      setNoteActionId(await submit({ kind: "note", text }));
      setNoteText("");
    } catch (error) {
      setNoteError(error instanceof Error ? error.message : "Could not send the note.");
    } finally {
      setNoteSending(false);
    }
  };

  const sendVote = async () => {
    if (!snapshot?.poll || !selectedOptionId) return;
    setVoteError(null);
    try {
      setVoteActionId(
        await submit({
          kind: "vote",
          poll_id: snapshot.poll.poll_id,
          option_id: selectedOptionId,
        }),
      );
    } catch (error) {
      setVoteError(error instanceof Error ? error.message : "Could not record the vote.");
    }
  };

  const requestDeletion = async () => {
    const entryId = noteOutcome?.entry_id;
    if (!entryId) return;
    setDeletionError(null);
    try {
      setDeletionActionId(
        await submit({ kind: "deletion_request", target_kind: "guestbook", target_id: entryId }),
      );
    } catch (error) {
      setDeletionError(error instanceof Error ? error.message : "Could not send the request.");
    }
  };

  // -- rendering ----------------------------------------------------------

  if (access === "checking") {
    return (
      <div className="center">
        <div className="spinner" />
        <p>Connecting to the door…</p>
      </div>
    );
  }

  if (access === "invalid" || access === "expired") {
    return (
      <>
        <h1>{access === "expired" ? "Session ended" : "Link not valid"}</h1>
        <div className="notice warn">
          <p>
            {access === "expired"
              ? "That door session has finished, so this page is closed. Ring again to start a new one."
              : "This link has expired or was mistyped. Scan the QR code on the door again."}
          </p>
        </div>
      </>
    );
  }

  const state = snapshot?.state ?? "VISITOR_MODE";
  const canWrite = state !== "SESSION_END" && state !== "IDLE";
  const results = snapshot?.poll_results ?? null;
  const totalVotes = results?.reduce((sum, row) => sum + row.votes, 0) ?? 0;

  return (
    <>
      <h1>At the door</h1>
      <p className="lede">{RING_STATUS_COPY[state] ?? "Connected."}</p>

      {/* Attribution is disclosed, never silent (E-23): someone who did not
          realise the door knew them must not find out from a stats page later. */}
      {snapshot?.attributed_to ? (
        <div className="notice" data-testid="attribution-notice">
          <p style={{ marginBottom: 0 }}>
            The door recognises you as <strong>{snapshot.attributed_to}</strong>, so your name will
            be attached to anything you leave below. Enrolled residents can ask the household admin
            to remove it.
          </p>
        </div>
      ) : null}

      <div className="card">
        <h2>Leave a note</h2>
        {noteOutcome?.status === "applied" ? (
          <div className="notice ok">
            <p>Your note was delivered. It may appear on the door&apos;s display.</p>
          </div>
        ) : noteOutcome?.status === "rejected" ? (
          <div className="notice warn">
            <p>{outcomeMessage(noteOutcome.reason)}</p>
          </div>
        ) : noteActionId ? (
          <div className="notice">
            <p>Sending to the door…</p>
          </div>
        ) : null}

        {!noteActionId && canWrite ? (
          <>
            <label htmlFor="note">Your message</label>
            <textarea
              id="note"
              value={noteText}
              maxLength={NOTE_MAX}
              rows={4}
              placeholder="Sorry I missed you…"
              onChange={(event) => setNoteText(event.target.value)}
            />
            <p className="hint">
              {noteText.length}/{NOTE_MAX} · shown on the door&apos;s display
            </p>
            {noteError ? (
              <div className="notice error" role="alert">
                <p>{noteError}</p>
              </div>
            ) : null}
            <div className="button-row">
              <button className="primary" onClick={() => void sendNote()} disabled={noteSending}>
                {noteSending ? "Sending…" : "Send note"}
              </button>
            </div>
          </>
        ) : null}

        {noteOutcome?.status === "applied" && noteOutcome.entry_id ? (
          <div className="button-row">
            {deletionOutcome?.status === "applied" ? (
              <p className="hint" style={{ margin: 0 }}>
                Deletion requested — the household will remove it.
              </p>
            ) : (
              <button className="secondary" onClick={() => void requestDeletion()} disabled={Boolean(deletionActionId)}>
                {deletionActionId ? "Requesting deletion…" : "Actually, please delete it"}
              </button>
            )}
            {deletionError ? (
              <div className="notice error" role="alert">
                <p>{deletionError}</p>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      {snapshot?.poll ? (
        <div className="card">
          <h2>{snapshot.poll.question}</h2>
          {voteOutcome?.status === "applied" || results ? (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {snapshot.poll.options.map((option) => {
                const votes = results?.find((row) => row.option_id === option.option_id)?.votes ?? 0;
                const share = totalVotes > 0 ? Math.round((votes / totalVotes) * 100) : 0;
                return (
                  <li key={option.option_id} style={{ marginBottom: "0.7rem" }}>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span>{option.label}</span>
                      <span style={{ color: "var(--text-dim)" }}>{share}%</span>
                    </div>
                    <div
                      style={{
                        height: "6px",
                        borderRadius: "999px",
                        background: "var(--border)",
                        marginTop: "0.25rem",
                      }}
                    >
                      <div
                        style={{
                          width: `${share}%`,
                          height: "100%",
                          borderRadius: "999px",
                          background: "var(--accent)",
                        }}
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <>
              {/* A vote the door refused used to show nothing at all: the button
                  sat on "Recording…" forever and the reason was never rendered. */}
              {voteOutcome?.status === "rejected" ? (
                <div className="notice warn">
                  <p>{outcomeMessage(voteOutcome.reason)}</p>
                </div>
              ) : null}
              {snapshot.poll.options.map((option) => (
                <label key={option.option_id} className="checkline">
                  <input
                    type="radio"
                    name="poll"
                    checked={selectedOptionId === option.option_id}
                    onChange={() => setSelectedOptionId(option.option_id)}
                  />
                  <span>{option.label}</span>
                </label>
              ))}
              {voteError ? (
                <div className="notice error" role="alert">
                  <p>{voteError}</p>
                </div>
              ) : null}
              {/* No button at all once the refusal is final: a disabled "Try
                  again" invites something that cannot work. The notice above
                  already says why. */}
              {voteRetryable || voteInFlight ? (
                <div className="button-row">
                  <button
                    className="primary"
                    onClick={() => void sendVote()}
                    disabled={!selectedOptionId || voteInFlight || !canWrite}
                  >
                    {voteInFlight
                      ? "Recording…"
                      : voteOutcome?.status === "rejected"
                        ? "Try again"
                        : "Vote"}
                  </button>
                </div>
              ) : null}
            </>
          )}
        </div>
      ) : null}

      <p className="footnote">
        This page keeps no account and no persistent identity. Your note and vote are shown on the
        door&apos;s display; nothing else about you is recorded. Ask the household to remove anything
        you left, any time.
      </p>
    </>
  );
}

function writeErrorMessage(code: string | undefined, status: number): string {
  switch (code) {
    case "session_expired":
      return "The door session ended while you were typing. Ring again to start a new one.";
    case "session_not_writable":
      return "The door session has moved on and is no longer accepting this.";
    case "session_not_found":
      return "This link is no longer valid. Scan the QR code on the door again.";
    case "rate_limited":
      return "Please slow down — try again in a bit.";
    case "invalid_action":
      return "That could not be sent as written. Try shortening it.";
    default:
      return status === 503
        ? "The door service is not reachable right now. Try again in a moment."
        : "Could not send that. Please try again.";
  }
}

/**
 * Outcomes door-api can return through the relay (`_visitor_reject_reason`).
 *
 * Exported for `tests/visitorFlow.test.tsx`, which scrapes that function off disk
 * and asserts none of its reasons falls through to the default — the same check
 * the enrolment page grew after `internal_error` reached a phone as generic
 * advice.
 */
export function outcomeMessage(reason: string | null): string {
  switch (reason) {
    case "rate_limited":
      return "The door is rate-limiting notes right now. Try again in a few minutes.";
    case "rejected_content":
      return "The door declined that message. Try rewording it.";
    case "session_mismatch":
      return "The door session changed before this arrived. Ring again to start a new one.";
    case "poll_closed":
      return "That poll closed before your vote arrived.";
    case "already_voted":
      return "A vote was already recorded for this session.";
    case "not_found":
      return "The door could not find what this refers to. It may have been removed already.";
    case "not_deletable":
      return "That is not something the door can delete from here. Ask the household directly.";
    case "empty_action":
      return "That arrived at the door empty. Try writing it again.";
    case "door_error":
      return "The door hit an error handling that, and nothing was saved. Try again in a moment.";
    default:
      // Echoed rather than swallowed: an unmapped reason is still worth showing,
      // because a visitor with no account has no other way to report it.
      return `The door could not accept that (${reason ?? "no reason given"}). Nothing was saved.`;
  }
}
