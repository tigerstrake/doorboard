import React from "react";
import { BigButton, StatusBadge } from "@doorboard/ui-kit";
import type {
  AmbientAircraftSummaryPayload,
  AmbientBirdSummaryPayload,
  AmbientFoodRecommendationPayload,
  AmbientPrinterStatusPayload,
  AmbientSatellitePassPayload,
} from "@doorboard/contracts";
import type { GuestbookEntry, Poll, PollResultRow } from "./socialApi";
import { GuestbookQuote, PollOptionRow } from "./SocialRenderers";
import { WALLBOARD_CHANNELS } from "./wallboardChannelModel";
import type { WallboardFocusChannel } from "./wallboardChannelModel";

// "Who's Stopped By" visitor collage — fed by the owner-only door-api
// GET /admin/visitor-collage endpoint (reached only via the secret /reveal
// page). Stats are count-only aggregates; photos are check-in photos the owner
// has approved for display in the gallery.
export interface VisitorCollageStats {
  total_checkins: number;
  checkins_this_year: number;
  unique_visitors: number;
  distinct_visitors: number;
  guest_count: number;
  most_frequent: { label: string | null; count: number } | null;
  first_checkin_at: string | null;
  most_recent_checkin_at: string | null;
}

export interface VisitorCollagePhoto {
  recording_id: string;
  thumbnail_path: string | null;
  label: string | null;
  created_at: string;
}

export interface VisitorCollage {
  stats: VisitorCollageStats;
  photos: VisitorCollagePhoto[];
}

const VISITOR_COLLAGE_EMPTY =
  "No check-ins yet — visitors can check in with a photo at the door.";

function formatCollageDate(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

function VisitorPhotoThumb({ photo }: { photo: VisitorCollagePhoto }) {
  // Gallery thumbnails are NAS-relative paths that the wallboard cannot fetch
  // directly, so we only render an <img> for absolute URLs and otherwise fall
  // back to an initial-based placeholder (same posture as the Moments tile).
  const src = photo.thumbnail_path;
  const isUrl = !!src && /^(https?:)?\/\//.test(src);
  const label = photo.label?.trim();
  if (isUrl) {
    return (
      <img
        className="visitor-collage-thumb"
        src={src as string}
        alt={label ? `Check-in photo — ${label}` : "Visitor check-in photo"}
        loading="lazy"
        onError={(e) => {
          e.currentTarget.style.display = "none";
        }}
      />
    );
  }
  return (
    <div className="visitor-collage-thumb visitor-collage-thumb--placeholder" aria-hidden="true">
      {(label?.[0] ?? "?").toUpperCase()}
    </div>
  );
}

function VisitorStatsPanel({ stats }: { stats: VisitorCollageStats }) {
  const lastVisit = formatCollageDate(stats.most_recent_checkin_at);
  return (
    <div className="visitor-collage-stats">
      <span className="visitor-collage-chip">
        <strong>{stats.unique_visitors}</strong> visitor{stats.unique_visitors === 1 ? "" : "s"}
      </span>
      <span className="visitor-collage-chip">
        <strong>{stats.checkins_this_year}</strong> this year
      </span>
      <span className="visitor-collage-chip">
        <strong>{stats.total_checkins}</strong> total visits
      </span>
      {stats.most_frequent && (
        <span className="visitor-collage-chip">
          Most frequent: <strong>{stats.most_frequent.label?.trim() || "Guest"}</strong> (
          {stats.most_frequent.count})
        </span>
      )}
      {lastVisit && (
        <span className="visitor-collage-chip">
          Last visit: <strong>{lastVisit}</strong>
        </span>
      )}
    </div>
  );
}

interface VisitorCollageContentProps {
  collage: VisitorCollage | null;
  maxPhotos?: number;
  variant?: "ambient" | "focus";
  /**
   * Whether to render the fun-stats chip panel below the photo grid. Defaults
   * to true. The owner-only `/reveal` page renders its own big celebratory
   * stats section and reuses this component only for the photo grid.
   */
  showStats?: boolean;
}

/**
 * Shared renderer for the visitor check-in photo grid + fun-stats chips. Reused
 * by the owner-only `/reveal` page (see RevealPage). This is deliberately NOT
 * wired into the public wallboard ambient rotation or channel launcher — the
 * collage is private all year and only shown at the reveal.
 */
export function VisitorCollageContent({
  collage,
  maxPhotos = 6,
  variant = "ambient",
  showStats = true,
}: VisitorCollageContentProps) {
  const stats = collage?.stats ?? null;
  const photos = collage?.photos ?? [];
  const hasActivity = (stats?.total_checkins ?? 0) > 0 || photos.length > 0;

  if (!hasActivity) {
    return <p className="visitor-collage-empty focus-empty">{VISITOR_COLLAGE_EMPTY}</p>;
  }

  return (
    <div className={`visitor-collage visitor-collage--${variant}`}>
      {photos.length > 0 ? (
        <div className="visitor-collage-grid">
          {photos.slice(0, maxPhotos).map((photo) => (
            <figure className="visitor-collage-cell" key={photo.recording_id}>
              <VisitorPhotoThumb photo={photo} />
              <figcaption>{photo.label?.trim() || "Guest"}</figcaption>
            </figure>
          ))}
        </div>
      ) : (
        <p className="visitor-collage-nophotos">
          Approved check-in photos will appear here as visitors opt in.
        </p>
      )}
      {showStats && stats && <VisitorStatsPanel stats={stats} />}
    </div>
  );
}

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
  ambient: {
    aircraft: AmbientAircraftSummaryPayload | null;
    birds: AmbientBirdSummaryPayload | null;
    birdCollageUrl: string;
    satellite: AmbientSatellitePassPayload | null;
    printer: AmbientPrinterStatusPayload | null;
    food: AmbientFoodRecommendationPayload | null;
    scoreboard: Array<{ score: number; occurredAt: string }> | null;
  };
  onReturnAmbient: () => void;
}

export function WallboardFocusedView({
  channel,
  poll,
  pollResults,
  guestbookEntries,
  moments,
  ambient,
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
      <main className="wallboard-focus-main">
        {renderFocusContent(channel, poll, pollResults, guestbookEntries, moments, ambient)}
      </main>
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
  moments: Array<{ recording_id: string; tags: string[]; approved_at: string | null }>,
  ambient: WallboardFocusedViewProps["ambient"]
) {
  const safeText = (value: string | null | undefined, maxLength = 80) =>
    (value ?? "").trim().replace(/\s+/g, " ").slice(0, maxLength);
  const clampPercentage = (value: number | null) =>
    value === null || !Number.isFinite(value) ? 0 : Math.min(100, Math.max(0, value));

  switch (channel) {
    case "aircraft":
      return ambient.aircraft ? (
        <div className="focus-list focus-list--large">
          {ambient.aircraft.nearby.slice(0, 8).map((aircraft, index) => (
            <div className="focus-row" key={`${aircraft.callsign}-${index}`}>
              <strong>{safeText(aircraft.callsign, 16) || "Aircraft"}</strong>
              <span>{aircraft.altitude_ft.toLocaleString()} ft</span>
              <span>{aircraft.distance_km} km away</span>
              <span>Heading {aircraft.heading}°</span>
            </div>
          ))}
          {ambient.aircraft.nearby.length === 0 && <p className="focus-empty">No nearby aircraft in the latest update.</p>}
        </div>
      ) : <p className="focus-empty">Aircraft data is unavailable.</p>;
    case "satellite":
      return ambient.satellite ? (
        <div className="focus-hero-stat">
          <p className="surface-eyebrow">Next visible pass</p>
          <strong>{ambient.satellite.satellite}</strong>
          <span>
            {new Date(ambient.satellite.rise_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            {" · "}
            {ambient.satellite.direction} · {ambient.satellite.max_elevation_deg}° max
          </span>
          <StatusBadge label={ambient.satellite.visible ? "available" : "unknown"} />
        </div>
      ) : <p className="focus-empty">Satellite pass data is unavailable.</p>;
    case "scoreboard":
      return ambient.scoreboard ? (
        <div className="focus-list focus-list--scores">
          {ambient.scoreboard.slice(0, 16).map((entry, index) => (
            <div className="focus-row" key={index}>
              <strong>Resident {index + 1}</strong>
              <span>{entry.score} pts</span>
            </div>
          ))}
        </div>
      ) : <p className="focus-empty">Scoreboard data is unavailable.</p>;
    case "birds":
      return (
        <div className="focus-list">
          {ambient.birds ? (
            <>
              <div className="focus-hero-stat focus-hero-stat--inline">
                <span>Total today</span>
                <strong>{ambient.birds.total_detections}</strong>
              </div>
              {ambient.birds.top_species.slice(0, 8).map((species, index) => (
                <div className="focus-row" key={`${species.name}-${index}`}>
                  <strong>{safeText(species.name) || "Unknown bird"}</strong>
                  <span>{species.count} detections</span>
                  <span>{(species.confidence_avg * 100).toFixed(0)}% confidence</span>
                </div>
              ))}
              {ambient.birds.top_species.length === 0 && <p className="focus-empty">No bird detections yet today.</p>}
            </>
          ) : <p className="focus-empty">Bird summary is unavailable.</p>}
          {ambient.birdCollageUrl && (
            <img
              className="bird-collage bird-collage--focus"
              src={ambient.birdCollageUrl}
              alt="Live bird collage from the window feeder"
              loading="lazy"
              onError={(e) => {
                e.currentTarget.style.display = "none";
              }}
            />
          )}
        </div>
      );
    case "printer":
      return ambient.printer ? (
        <div className="focus-printer">
          <p className="surface-eyebrow">{ambient.printer.state}</p>
          <h2>{ambient.printer.job_name ? safeText(ambient.printer.job_name) : "No active print"}</h2>
          <div className="focus-progress" aria-label={`${ambient.printer.progress_pct ?? 0}% complete`}>
            <span style={{ width: `${clampPercentage(ambient.printer.progress_pct)}%` }} />
          </div>
          <strong>{ambient.printer.progress_pct === null ? "Progress unavailable" : `${ambient.printer.progress_pct}% complete`}</strong>
        </div>
      ) : <p className="focus-empty">Printer status is unavailable.</p>;
    case "food":
      return ambient.food ? (
        <div className="focus-hero-stat">
          <p className="surface-eyebrow">{safeText(ambient.food.provider, 40)}</p>
          <strong>{safeText(ambient.food.title)}</strong>
          <span>{safeText(ambient.food.detail, 160)}</span>
        </div>
      ) : <p className="focus-empty">Food recommendation is unavailable.</p>;
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
