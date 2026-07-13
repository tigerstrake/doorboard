import React from "react";
import { BigButton, StatusBadge } from "@doorboard/ui-kit";
import type { GuestbookEntry, Poll, PollResultRow } from "./socialApi";
import { GuestbookQuote, PollOptionRow } from "./SocialRenderers";
import {
  aircraftFixture,
  birdFixture,
  foodFixture,
  printerFixture,
  satelliteFixture,
  scoreboardFixture,
} from "./fixtures";
import { WALLBOARD_CHANNELS } from "./wallboardChannelModel";
import type { WallboardFocusChannel } from "./wallboardChannelModel";

interface WallboardLauncherProps {
  selectedChannel: "ambient" | WallboardFocusChannel;
  onSelect: (channel: "ambient" | WallboardFocusChannel) => void;
}

export function WallboardLauncher({ selectedChannel, onSelect }: WallboardLauncherProps) {
  return (
    <div className="wallboard-launcher-grid" role="list" aria-label="Wallboard channels">
      {WALLBOARD_CHANNELS.map((channel) => (
        <button
          key={channel.id}
          type="button"
          className="wallboard-launcher-card"
          aria-pressed={selectedChannel === channel.id}
          onClick={() => onSelect(channel.id)}
        >
          <span className="wallboard-launcher-card__eyebrow">{channel.eyebrow}</span>
          <strong>{channel.title}</strong>
          <span>{channel.description}</span>
        </button>
      ))}
    </div>
  );
}

interface WallboardFocusedViewProps {
  channel: WallboardFocusChannel;
  poll: Poll | null;
  pollResults: PollResultRow[] | null;
  guestbookEntries: GuestbookEntry[];
  moments: Array<{ recording_id: string; tags: string[]; approved_at: string | null }>;
  onReturnAmbient: () => void;
}

export function WallboardFocusedView({
  channel,
  poll,
  pollResults,
  guestbookEntries,
  moments,
  onReturnAmbient,
}: WallboardFocusedViewProps) {
  const title = WALLBOARD_CHANNELS.find((item) => item.id === channel)?.title ?? "Focused view";

  return (
    <div className={`wallboard-focus-view wallboard-focus-view--${channel} db-app-theme`}>
      <header className="wallboard-focus-header">
        <div>
          <p className="surface-eyebrow">Wallboard channel</p>
          <h1>{title}</h1>
        </div>
        <BigButton className="wallboard-focus-return" onClick={onReturnAmbient}>
          Ambient grid
        </BigButton>
      </header>
      <main className="wallboard-focus-main">{renderFocusContent(channel, poll, pollResults, guestbookEntries, moments)}</main>
      <footer className="wallboard-focus-footer">
        Focused from DoorPad. Returns to ambient automatically.
      </footer>
    </div>
  );
}

function renderFocusContent(
  channel: WallboardFocusChannel,
  poll: Poll | null,
  pollResults: PollResultRow[] | null,
  guestbookEntries: GuestbookEntry[],
  moments: Array<{ recording_id: string; tags: string[]; approved_at: string | null }>
) {
  switch (channel) {
    case "aircraft":
      return (
        <div className="focus-list focus-list--large">
          {aircraftFixture.nearby.map((aircraft) => (
            <div className="focus-row" key={aircraft.callsign}>
              <strong>{aircraft.callsign}</strong>
              <span>{aircraft.altitude_ft.toLocaleString()} ft</span>
              <span>{aircraft.distance_km} km away</span>
              <span>Heading {aircraft.heading}°</span>
            </div>
          ))}
        </div>
      );
    case "satellite":
      return (
        <div className="focus-hero-stat">
          <p className="surface-eyebrow">Next visible pass</p>
          <strong>{satelliteFixture.satellite}</strong>
          <span>
            {new Date(satelliteFixture.rise_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            {" · "}
            {satelliteFixture.direction} · {satelliteFixture.max_elevation_deg}° max
          </span>
          <StatusBadge label={satelliteFixture.visible ? "available" : "unknown"} />
        </div>
      );
    case "scoreboard":
      return (
        <div className="focus-list focus-list--scores">
          {scoreboardFixture.scores.map((score, index) => (
            <div className="focus-row" key={score.name}>
              <strong>Resident {index + 1}</strong>
              <span>{score.score} pts</span>
            </div>
          ))}
        </div>
      );
    case "birds":
      return (
        <div className="focus-list">
          <div className="focus-hero-stat focus-hero-stat--inline">
            <span>Total today</span>
            <strong>{birdFixture.total_detections}</strong>
          </div>
          {birdFixture.top_species.map((species) => (
            <div className="focus-row" key={species.name}>
              <strong>{species.name}</strong>
              <span>{species.count} detections</span>
              <span>{(species.confidence_avg * 100).toFixed(0)}% confidence</span>
            </div>
          ))}
        </div>
      );
    case "printer":
      return (
        <div className="focus-printer">
          <p className="surface-eyebrow">{printerFixture.state}</p>
          <h2>{printerFixture.job_name}</h2>
          <div className="focus-progress" aria-label={`${printerFixture.progress_pct}% complete`}>
            <span style={{ width: `${printerFixture.progress_pct}%` }} />
          </div>
          <strong>{printerFixture.progress_pct}% complete</strong>
        </div>
      );
    case "food":
      return (
        <div className="focus-hero-stat">
          <p className="surface-eyebrow">{foodFixture.provider}</p>
          <strong>{foodFixture.title}</strong>
          <span>{foodFixture.detail}</span>
        </div>
      );
    case "poll":
      return poll ? (
        <div className="focus-list">
          <h2>{poll.question}</h2>
          {poll.options.map((option) => (
            <PollOptionRow
              key={option.id}
              text={option.text}
              votes={pollResults?.find((row) => row.option_id === option.id)?.votes ?? 0}
            />
          ))}
        </div>
      ) : (
        <p className="focus-empty">No poll is running right now.</p>
      );
    case "guestbook":
      return guestbookEntries.length > 0 ? (
        <div className="focus-guestbook">
          {guestbookEntries.slice(0, 4).map((entry) => (
            <GuestbookQuote key={entry.id} text={entry.text} authorLabel={entry.author_label} />
          ))}
        </div>
      ) : (
        <p className="focus-empty">No approved guestbook notes yet.</p>
      );
    case "moments":
      return moments.length > 0 ? (
        <div className="focus-moments">
          {moments.slice(0, 6).map((moment) => (
            <div className="focus-moment" key={moment.recording_id}>
              <strong>{moment.tags.length > 0 ? moment.tags.join(", ") : "Photo Booth"}</strong>
              <span>{moment.recording_id.slice(0, 8)}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="focus-empty">No approved moments yet.</p>
      );
  }
}
