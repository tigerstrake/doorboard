import type { Poll, PollResultRow } from "./socialApi";

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
  const votesFor = (optionId: string) =>
    pollResults?.find((row) => row.option_id === optionId)?.votes ?? 0;
  const tallies = poll.options.map((option) => votesFor(option.id));
  const totalVotes = tallies.reduce((sum, votes) => sum + votes, 0);
  const maxVotes = tallies.length > 0 ? Math.max(...tallies) : 0;

  return (
    <div
      className={className ? `poll-focus__options ${className}` : "poll-focus__options"}
      data-testid="poll-result-bars"
    >
      {poll.options.map((option) => {
        const votes = votesFor(option.id);
        const pct = totalVotes > 0 ? (votes / totalVotes) * 100 : 0;
        // With no votes at all nobody leads; otherwise ties both highlight, which is honest.
        const isLeader = totalVotes > 0 && votes === maxVotes;
        const isMine = votedOptionId !== null && votedOptionId === option.id;
        return (
          <div
            className={[
              "poll-focus__row",
              isLeader ? "poll-focus__row--leader" : "",
              isMine ? "poll-focus__row--mine" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            key={option.id}
          >
            <div className="poll-focus__row-head">
              <span className="poll-focus__option">
                {option.text}
                {isMine ? <span className="poll-focus__mine-tag"> your vote</span> : null}
              </span>
              <span className="poll-focus__count">
                <strong>{votes}</strong> {votes === 1 ? "vote" : "votes"} · {pct.toFixed(0)}%
              </span>
            </div>
            <div
              className="poll-focus__bar"
              role="progressbar"
              aria-valuenow={Math.round(pct)}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={option.text}
            >
              <span style={{ width: `${pct}%` }} />
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
