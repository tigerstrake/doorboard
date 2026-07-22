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
import { GuestbookQuote } from "./SocialRenderers";
import { AircraftFocusPanel } from "./wallboard/AircraftFocusPanel";
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

interface WallboardFocusSplitProps {
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
  /**
   * The remaining ambient tiles (everything except the focused channel),
   * rendered live and shrunk into the secondary rail beside the focus panel.
   * Optional so the content renderer can be exercised in isolation by tests.
   */
  secondary?: React.ReactNode;
  onReturnAmbient: () => void;
}

/**
 * The wallboard "focus a tile" experience. Instead of a full-screen single
 * channel takeover, the focused channel grows into a large panel (~half the
 * viewport) showing an expanded view of the data we already hold, while every
 * other tile shrinks into a live secondary rail that stays visible around it.
 * The grow/shrink is animated with GPU-friendly transform/opacity (see
 * `.wallboard-focus-panel` / `.wallboard-focus-rail` in App.css), and the whole
 * surface crossfades in/out via the parent CrossfadeSwitch.
 */
export function WallboardFocusSplit({
  channel,
  poll,
  pollResults,
  guestbookEntries,
  moments,
  ambient,
  secondary,
  onReturnAmbient,
}: WallboardFocusSplitProps) {
  const definition = WALLBOARD_CHANNELS.find((item) => item.id === channel);
  const title = definition?.title ?? "Focused view";
  const eyebrow = definition?.eyebrow ?? "Wallboard channel";

  return (
    <div
      className={`wallboard-focus-split wallboard-focus-split--${channel} db-app-theme`}
      data-testid="wallboard-focus-split"
    >
      <header className="wallboard-focus-header">
        <div>
          <p className="surface-eyebrow">Focused · {eyebrow}</p>
          <h1>{title}</h1>
        </div>
        <BigButton className="wallboard-focus-return" onClick={onReturnAmbient}>
          Ambient grid
        </BigButton>
      </header>
      <div className="wallboard-focus-layout">
        <main className="wallboard-focus-panel" data-testid="wallboard-focus-panel">
          {renderFocusContent(channel, poll, pollResults, guestbookEntries, moments, ambient)}
        </main>
        {secondary != null && (
          <aside
            className="wallboard-focus-rail"
            aria-label="Other wallboard tiles"
            data-testid="wallboard-focus-rail"
          >
            {secondary}
          </aside>
        )}
      </div>
      <footer className="wallboard-focus-footer">
        Focused from DoorPad. Returns to the ambient grid automatically.
      </footer>
    </div>
  );
}

const focusSafeText = (value: string | null | undefined, maxLength = 80) =>
  (value ?? "").trim().replace(/\s+/g, " ").slice(0, maxLength);

const clampPercentage = (value: number | null) =>
  value === null || !Number.isFinite(value) ? 0 : Math.min(100, Math.max(0, value));

/**
 * Large, centred "nothing here yet" state shared by every focus channel that
 * has no data to show. Keeps sparse channels (scoreboard/food/printer/…) from
 * rendering as a cramped one-liner at hallway scale, matching the deliberate
 * emptiness of the rest of the HUD design system.
 */
function FocusEmpty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="focus-empty-state" data-testid="focus-empty-state">
      <span className="focus-empty-state__glyph" aria-hidden="true" />
      <p className="focus-empty-state__title">{title}</p>
      {hint ? <p className="focus-empty-state__hint">{hint}</p> : null}
    </div>
  );
}

// 16-point compass rose → bearing in degrees clockwise from due north. Used to
// aim the sky-compass needle from the satellite pass's rise direction label.
const COMPASS_BEARINGS: Record<string, number> = {
  N: 0, NNE: 22.5, NE: 45, ENE: 67.5,
  E: 90, ESE: 112.5, SE: 135, SSE: 157.5,
  S: 180, SSW: 202.5, SW: 225, WSW: 247.5,
  W: 270, WNW: 292.5, NW: 315, NNW: 337.5,
};

function bearingForDirection(direction: string): number | null {
  const key = direction.trim().toUpperCase();
  return key in COMPASS_BEARINGS ? COMPASS_BEARINGS[key] : null;
}

/**
 * A clean SVG compass rose with cardinal ticks and a needle aimed at the
 * satellite's rise direction. Degrades to a centred dot (no needle) when the
 * direction label isn't a recognised compass point.
 */
function SkyCompass({ direction }: { direction: string }) {
  const bearing = bearingForDirection(direction);
  const cardinals: Array<{ label: string; x: number; y: number }> = [
    { label: "N", x: 100, y: 22 },
    { label: "E", x: 180, y: 106 },
    { label: "S", x: 100, y: 188 },
    { label: "W", x: 20, y: 106 },
  ];
  return (
    <svg
      className="sky-compass"
      viewBox="0 0 200 200"
      role="img"
      aria-label={
        bearing === null
          ? "Rise direction unavailable"
          : `Rises to the ${direction}`
      }
      data-testid="sky-compass"
    >
      <circle className="sky-compass__ring" cx="100" cy="100" r="82" />
      <circle className="sky-compass__ring sky-compass__ring--inner" cx="100" cy="100" r="58" />
      {Array.from({ length: 16 }).map((_, index) => {
        const angle = (index * 22.5 * Math.PI) / 180;
        const isMajor = index % 4 === 0;
        const outer = 82;
        const inner = isMajor ? 68 : 74;
        return (
          <line
            key={index}
            className={`sky-compass__tick${isMajor ? " sky-compass__tick--major" : ""}`}
            x1={100 + outer * Math.sin(angle)}
            y1={100 - outer * Math.cos(angle)}
            x2={100 + inner * Math.sin(angle)}
            y2={100 - inner * Math.cos(angle)}
          />
        );
      })}
      {cardinals.map((point) => (
        <text
          key={point.label}
          className="sky-compass__cardinal"
          x={point.x}
          y={point.y}
          textAnchor="middle"
          dominantBaseline="middle"
        >
          {point.label}
        </text>
      ))}
      {bearing === null ? (
        <circle className="sky-compass__center" cx="100" cy="100" r="7" />
      ) : (
        <g transform={`rotate(${bearing} 100 100)`}>
          <polygon className="sky-compass__needle" points="100,26 111,104 100,92 89,104" />
          <circle className="sky-compass__center" cx="100" cy="100" r="7" />
        </g>
      )}
    </svg>
  );
}

function SatelliteFocusPanel({ payload }: { payload: AmbientSatellitePassPayload }) {
  const riseTime = new Date(payload.rise_at).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  const maxElevation = Number.isFinite(payload.max_elevation_deg)
    ? Math.round(payload.max_elevation_deg)
    : null;
  return (
    <div className="satellite-focus" data-testid="satellite-focus">
      <header className="satellite-focus__head">
        <p className="surface-eyebrow">Next visible pass</p>
        <strong className="satellite-focus__name">{payload.satellite}</strong>
        <p className="satellite-focus__meta">
          {payload.direction} · {maxElevation ?? payload.max_elevation_deg}° max
        </p>
        <StatusBadge label={payload.visible ? "available" : "unknown"} />
      </header>
      <div className="satellite-focus__body">
        <SkyCompass direction={payload.direction} />
        <dl className="satellite-focus__stats">
          <div className="satellite-focus__stat satellite-focus__stat--hero">
            <dt>Rise time</dt>
            <dd>{riseTime}</dd>
          </div>
          <div className="satellite-focus__stat">
            <dt>Direction</dt>
            <dd>{payload.direction}</dd>
          </div>
          <div className="satellite-focus__stat">
            <dt>Max elevation</dt>
            <dd>{maxElevation ?? payload.max_elevation_deg}°</dd>
          </div>
        </dl>
      </div>
    </div>
  );
}

function PollFocusPanel({
  poll,
  pollResults,
}: {
  poll: Poll;
  pollResults: PollResultRow[] | null;
}) {
  const votesFor = (optionId: string) =>
    pollResults?.find((row) => row.option_id === optionId)?.votes ?? 0;
  const tallies = poll.options.map((option) => votesFor(option.id));
  const totalVotes = tallies.reduce((sum, votes) => sum + votes, 0);
  const maxVotes = tallies.length > 0 ? Math.max(...tallies) : 0;

  return (
    <div className="poll-focus" data-testid="poll-focus">
      <h2 className="poll-focus__question">{poll.question}</h2>
      <div className="poll-focus__options">
        {poll.options.map((option) => {
          const votes = votesFor(option.id);
          const pct = totalVotes > 0 ? (votes / totalVotes) * 100 : 0;
          const isLeader = totalVotes > 0 && votes === maxVotes;
          return (
            <div
              className={`poll-focus__row${isLeader ? " poll-focus__row--leader" : ""}`}
              key={option.id}
            >
              <div className="poll-focus__row-head">
                <span className="poll-focus__option">{option.text}</span>
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
      <p className="poll-focus__total">
        {totalVotes} total {totalVotes === 1 ? "vote" : "votes"}
      </p>
    </div>
  );
}

function renderFocusContent(
  channel: WallboardFocusChannel,
  poll: Poll | null,
  pollResults: PollResultRow[] | null,
  guestbookEntries: GuestbookEntry[],
  moments: Array<{ recording_id: string; tags: string[]; approved_at: string | null }>,
  ambient: WallboardFocusSplitProps["ambient"]
) {
  switch (channel) {
    case "aircraft":
      return ambient.aircraft ? (
        <AircraftFocusPanel payload={ambient.aircraft} />
      ) : (
        <FocusEmpty title="Aircraft data is unavailable." hint="Waiting for the next overhead sweep." />
      );
    case "satellite":
      return ambient.satellite ? (
        <SatelliteFocusPanel payload={ambient.satellite} />
      ) : (
        <FocusEmpty
          title="Satellite pass data is unavailable."
          hint="The next visible pass will appear here."
        />
      );
    case "scoreboard":
      return ambient.scoreboard && ambient.scoreboard.length > 0 ? (
        <div className="focus-list focus-list--scores">
          {ambient.scoreboard.slice(0, 16).map((entry, index) => (
            <div
              className={`focus-row${index === 0 ? " focus-row--leader" : ""}`}
              key={index}
            >
              <strong>
                <span className="focus-rank">{index + 1}</span>Resident {index + 1}
              </strong>
              <span>{entry.score} pts</span>
            </div>
          ))}
        </div>
      ) : (
        <FocusEmpty title="No scores yet." hint="The room scoreboard is still warming up." />
      );
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
                  <strong>{focusSafeText(species.name) || "Unknown bird"}</strong>
                  <span>{species.count} detections</span>
                  <span>{(species.confidence_avg * 100).toFixed(0)}% confidence</span>
                </div>
              ))}
              {ambient.birds.top_species.length === 0 && (
                <FocusEmpty title="No bird detections yet today." hint="Species will list here as they arrive." />
              )}
            </>
          ) : (
            <FocusEmpty title="Bird summary is unavailable." hint="Waiting for the window feeder." />
          )}
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
          <h2>{ambient.printer.job_name ? focusSafeText(ambient.printer.job_name) : "No active print"}</h2>
          <div className="focus-progress" aria-label={`${ambient.printer.progress_pct ?? 0}% complete`}>
            <span style={{ width: `${clampPercentage(ambient.printer.progress_pct)}%` }} />
          </div>
          <strong>{ambient.printer.progress_pct === null ? "Progress unavailable" : `${ambient.printer.progress_pct}% complete`}</strong>
        </div>
      ) : (
        <FocusEmpty title="Printer status is unavailable." hint="No job reported from the lab printer." />
      );
    case "food":
      return ambient.food ? (
        <div className="focus-hero-stat">
          <p className="surface-eyebrow">{focusSafeText(ambient.food.provider, 40)}</p>
          <strong>{focusSafeText(ambient.food.title)}</strong>
          <span>{focusSafeText(ambient.food.detail, 160)}</span>
        </div>
      ) : (
        <FocusEmpty title="No food recommendation yet." hint="Today's pick will appear here." />
      );
    case "poll":
      return poll ? (
        <PollFocusPanel poll={poll} pollResults={pollResults} />
      ) : (
        <FocusEmpty title="No poll is running right now." hint="A new question will show up when one opens." />
      );
    case "guestbook":
      return guestbookEntries.length > 0 ? (
        <div className="guestbook-focus" data-testid="guestbook-focus">
          {guestbookEntries.slice(0, 5).map((entry) => (
            <GuestbookQuote key={entry.id} text={entry.text} authorLabel={entry.author_label} />
          ))}
        </div>
      ) : (
        <FocusEmpty title="No approved guestbook notes yet." hint="Visitors can sign the guestbook at the door." />
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
        <FocusEmpty title="No approved moments yet." hint="Photo-booth highlights land here once approved." />
      );
  }
}
