import type { Poll, PollResultRow } from "./socialApi";

export interface PollShare {
  optionId: string;
  text: string;
  votes: number;
  /** Share of the total cast, so the shares sum to 100. */
  pct: number;
  isLeader: boolean;
}

/**
 * The tallies as shares. Shared by the two surfaces that draw them, which render very
 * differently — the wallboard as a read-only chart, the doorpad as tappable vote buttons
 * with the bar as their fill — but must never disagree on the numbers.
 *
 * Percentages are of the total cast rather than of the leader: "what share of the room" is
 * the question, and scaling to the leader makes any lead at all look like a landslide.
 */
export function pollShares(poll: Poll, pollResults: PollResultRow[] | null): PollShare[] {
  const votesFor = (optionId: string) =>
    pollResults?.find((row) => row.option_id === optionId)?.votes ?? 0;
  const tallies = poll.options.map((option) => votesFor(option.id));
  const totalVotes = tallies.reduce((sum, votes) => sum + votes, 0);
  const maxVotes = tallies.length > 0 ? Math.max(...tallies) : 0;
  return poll.options.map((option) => {
    const votes = votesFor(option.id);
    return {
      optionId: option.id,
      text: option.text,
      votes,
      pct: totalVotes > 0 ? (votes / totalVotes) * 100 : 0,
      // With no votes at all nobody leads; otherwise ties both highlight, which is honest.
      isLeader: totalVotes > 0 && votes === maxVotes,
    };
  });
}

/**
 * The live standing of a poll, as one bar per option.
 *
 * Extracted from the wallboard's focus panel so the doorpad can show the same graph rather
 * than a second, subtly different one. Both surfaces sit at the same door, a metre apart —
 * two renderings of the same tally that disagreed on rounding or on who is leading would be
 * visible side by side.
 *
 * Percentages are of the total cast, so they sum to 100 and a bar answers "what share",
 * not "how does this compare to the biggest" — the latter reads as a runaway lead whenever
 * one option is ahead at all.
 */
export function PollResultBars({
  poll,
  pollResults,
  votedOptionId = null,
  className,
}: {
  poll: Poll;
  pollResults: PollResultRow[] | null;
  /** The option this visitor chose, marked so they can find their own vote. */
  votedOptionId?: string | null;
  className?: string;
}) {
  return (
    <div
      className={className ? `poll-focus__options ${className}` : "poll-focus__options"}
      data-testid="poll-result-bars"
    >
      {pollShares(poll, pollResults).map((share) => {
        const isMine = votedOptionId !== null && votedOptionId === share.optionId;
        return (
          <div
            className={[
              "poll-focus__row",
              share.isLeader ? "poll-focus__row--leader" : "",
              isMine ? "poll-focus__row--mine" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            key={share.optionId}
          >
            <div className="poll-focus__row-head">
              <span className="poll-focus__option">
                {share.text}
                {isMine ? <span className="poll-focus__mine-tag"> your vote</span> : null}
              </span>
              <span className="poll-focus__count">
                <strong>{share.votes}</strong> {share.votes === 1 ? "vote" : "votes"} ·{" "}
                {share.pct.toFixed(0)}%
              </span>
            </div>
            <div
              className="poll-focus__bar"
              role="progressbar"
              aria-valuenow={Math.round(share.pct)}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={share.text}
            >
              <span style={{ width: `${share.pct}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function pollTotalVotes(poll: Poll, pollResults: PollResultRow[] | null): number {
  return poll.options.reduce(
    (sum, option) =>
      sum + (pollResults?.find((row) => row.option_id === option.id)?.votes ?? 0),
    0
  );
}
