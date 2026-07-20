import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Tile,
  StatusBadge,
  BigButton,
  CountdownAutoReset,
  QRPlaceholder,
  LiveVideoPreview,
  CrossfadeSwitch,
  SessionState,
  Gauge,
} from "@doorboard/ui-kit";
import { DoorboardEventClient, uuidv7 } from "@doorboard/event-client";
import type {
  AmbientAircraftSummaryPayload,
  AmbientBirdSummaryPayload,
  AmbientFoodRecommendationPayload,
  AmbientPrinterStatusPayload,
  AmbientSatellitePassPayload,
  DoorboardEvent,
  PresenceLabel,
} from "@doorboard/contracts";
import { WallboardVisitorMode } from "./wallboard/WallboardVisitorMode";
import {
  presenceFixture,
  birdFixture,
  aircraftFixture,
  satelliteFixture,
  printerFixture,
  moodFixture,
  scoreboardFixture,
  foodFixture,
  aboutFixture,
} from "./fixtures";
import { API_BASE, wsUrlFromApiBase } from "./apiBase";
import { socialApi, ApiError } from "./socialApi";
import type { GuestbookEntry, Poll, PollResultRow } from "./socialApi";
import { AdminSocialPanel } from "./AdminSocialPanel";
import { AdminEnrollmentPanel } from "./AdminEnrollmentPanel";
import { AdminAboutPanel } from "./AdminAboutPanel";
import { VisitorPage } from "./VisitorPage";
import { RevealPage } from "./RevealPage";
import { GuestbookQuote, PollOptionRow } from "./SocialRenderers";
import { WallboardFocusedView, WallboardLauncher } from "./wallboardChannels";
import { OnScreenKeyboard } from "./OnScreenKeyboard";
import {
  WALLBOARD_CONTROL_EVENT,
  WALLBOARD_CONTROL_STORAGE_KEY,
  createWallboardFocusRequest,
  isWallboardFocusRequest,
} from "./wallboardChannelModel";
import type { WallboardFocusChannel, WallboardFocusRequest } from "./wallboardChannelModel";

// Import CSS
import "@doorboard/ui-kit/index.css";
import "./App.css";

const CANNED_GUESTBOOK_PHRASES = ["Hey, stopped by!", "Call me later!", "Awesome door board!"];

// Content this kiosk session created, so the Privacy screen can offer a
// real deletion path without needing a login/identity system.
type MyContentRef = { kind: "guestbook" | "checkin"; id: string; label: string };
type MyContentStore = Record<string, MyContentRef[]>;
const MY_CONTENT_KEY = "doorboard_my_social_content_v2";
const ADMIN_TOKEN_KEY = "doorboard_admin_social_token";
const MAX_STORED_VISITOR_SESSIONS = 16;
const MAX_CONTENT_ITEMS_PER_SESSION = 32;

function adminHeaders(json = false): Record<string, string> {
  const token = window.localStorage.getItem(ADMIN_TOKEN_KEY);
  return {
    ...(json ? { "Content-Type": "application/json" } : {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

function isMyContentRef(value: unknown): value is MyContentRef {
  if (value === null || typeof value !== "object") return false;
  const item = value as Partial<MyContentRef>;
  return (
    (item.kind === "guestbook" || item.kind === "checkin") &&
    typeof item.id === "string" &&
    item.id.length > 0 &&
    item.id.length <= 128 &&
    typeof item.label === "string" &&
    item.label.length <= 160
  );
}

function parseMyContentStore(raw: string | null): MyContentStore {
  try {
    const parsed = raw ? (JSON.parse(raw) as unknown) : {};
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed)
        .flatMap(([sessionId, items]) => {
          if (!Array.isArray(items)) return [];
          const validItems = items.filter(isMyContentRef).slice(-MAX_CONTENT_ITEMS_PER_SESSION);
          return validItems.length > 0 ? [[sessionId, validItems] as const] : [];
        })
        .slice(-MAX_STORED_VISITOR_SESSIONS)
    );
  } catch {
    return {};
  }
}

function loadMyContent(sessionId: string): MyContentRef[] {
  const stored = parseMyContentStore(window.localStorage.getItem(MY_CONTENT_KEY));
  return Array.isArray(stored[sessionId]) ? stored[sessionId] : [];
}

function saveMyContent(sessionId: string, items: MyContentRef[]): void {
  try {
    const stored = parseMyContentStore(window.localStorage.getItem(MY_CONTENT_KEY));
    if (items.length > 0) stored[sessionId] = items;
    else delete stored[sessionId];
    const bounded = Object.fromEntries(
      Object.entries(stored).slice(-MAX_STORED_VISITOR_SESSIONS)
    );
    window.localStorage.setItem(MY_CONTENT_KEY, JSON.stringify(bounded));
  } catch {
    // Storage can be disabled or full on a locked-down kiosk. The durable API
    // action has already succeeded, so local deletion shortcuts degrade away.
  }
}

function apiErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return "Please slow down — too many submissions. Try again in a bit.";
    return err.message || fallback;
  }
  return fallback;
}

const FEATURE_PHOTOBOOTH =
  ((import.meta.env.VITE_FEATURE_PHOTOBOOTH as string | undefined) ??
    (import.meta.env.FEATURE_PHOTOBOOTH as string | undefined)) === "true";
const DEV_TOOLS_ENABLED =
  ((import.meta.env.VITE_DOOR_UI_DEV_TOOLS as string | undefined) ??
    (import.meta.env.VITE_ENABLE_SIM_PANEL as string | undefined)) === "true";
const MOCK_AMBIENT_ENABLED =
  (import.meta.env.VITE_AMBIENT_MOCK as string | undefined) === "true" || DEV_TOOLS_ENABLED;
const configuredAircraftAlertDistanceKm = Number(
  (import.meta.env.VITE_AIRCRAFT_ALERT_DISTANCE_KM as string | undefined) ?? "3"
);
// Live bird collage image (e.g. the AvianVisitors frame PNG served by the
// window bird-Pi). Empty = no collage shown. Rendered under the bird stats.
const BIRD_COLLAGE_URL = (import.meta.env.VITE_BIRD_COLLAGE_URL as string | undefined) ?? "";
// Build-time room label shown in the DoorPad and Wallboard headings. Defaults
// to "304" so existing deployments/tests are unchanged; set VITE_ROOM_LABEL to
// re-brand a different room without touching code.
const ROOM_LABEL =
  ((import.meta.env.VITE_ROOM_LABEL as string | undefined) ?? "304").trim() || "304";
// Optional, comma-separated resident names surfaced as a subtle subtitle under
// the room heading (e.g. "Tiger, Adam" -> "Tiger & Adam"). Empty renders
// nothing extra. Also seeds later collage/presence use.
const RESIDENTS = ((import.meta.env.VITE_RESIDENTS as string | undefined) ?? "")
  .split(",")
  .map((name) => name.trim())
  .filter((name) => name.length > 0);
// "Tiger", "Tiger & Adam", "Tiger, Adam & Sam" — a friendly join for display.
const RESIDENTS_LABEL =
  RESIDENTS.length === 0
    ? ""
    : RESIDENTS.length === 1
      ? RESIDENTS[0]
      : `${RESIDENTS.slice(0, -1).join(", ")} & ${RESIDENTS[RESIDENTS.length - 1]}`;
const AIRCRAFT_ALERT_DISTANCE_KM =
  Number.isFinite(configuredAircraftAlertDistanceKm) && configuredAircraftAlertDistanceKm > 0
    ? configuredAircraftAlertDistanceKm
    : 3;
const MAX_PRESENCE_SUBJECTS = 8;
const MAX_SCOREBOARD_ENTRIES = 16;

function safeDisplayText(value: unknown, maxLength = 80): string {
  if (typeof value !== "string") return "";
  const normalized = value.trim().replace(/\s+/g, " ");
  return normalized.slice(0, maxLength);
}

function clampPercentage(value: number | null): number {
  if (value === null || !Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, value));
}

function isFiniteNonNegative(value: number): boolean {
  return Number.isFinite(value) && value >= 0;
}

type DoorPadScreen =
  | "home"
  | "ringing"
  | "message"
  | "photo"
  | "guestbook"
  | "poll"
  | "checkin"
  | "privacy"
  | "remote";
type VideoStep = "offer" | "countdown" | "recording" | "review" | "saved" | "qr";
type PhotoStep = "offer" | "countdown" | "review" | "saved";
// Lifecycle of the auto-captured post-bell check-in photo:
// idle -> capturing -> ready -> (saving -> saved | cleared). "cleared" is a
// terminal state used for both "No thanks" and discard-on-abandon so the
// auto-capture effect (which only fires from "idle") never re-triggers.
type PostRingPhotoStatus =
  | "idle"
  | "capturing"
  | "ready"
  | "unavailable"
  | "saving"
  | "saved"
  | "cleared";
// Auto-capture fires shortly after the ringing screen appears so the ring
// request settles first and the capture feels intentional, not jarring.
const POST_RING_PHOTO_DELAY_MS = 600;

interface DoorApiSnapshot {
  accepted?: boolean;
  session?: {
    state?: SessionState;
    session_id?: string | null;
    display_name?: string | null;
    profile_id?: string | null;
  };
  config?: { max_recording_s?: number };
}

interface TimedAmbient<T> {
  payload: T;
  occurredAt: string;
}

interface ScoreboardEntry {
  score: number;
  occurredAt: string;
}

interface AmbientAlert {
  id: string;
  kind: "aircraft" | "satellite" | "bird" | "printer";
  title: string;
  detail: string;
  priority: number;
}

function doorPadRouteForState(state: SessionState): { screen: DoorPadScreen; video: VideoStep } {
  if (state === "IDLE" || state === "SESSION_END") return { screen: "home", video: "offer" };
  if (state === "VIDEO_MESSAGE_OFFERED") return { screen: "message", video: "offer" };
  if (state === "VIDEO_MESSAGE_RECORDING") return { screen: "message", video: "recording" };
  if (state === "VIDEO_MESSAGE_REVIEW") return { screen: "message", video: "review" };
  if (state === "VIDEO_MESSAGE_SAVED") return { screen: "message", video: "saved" };
  return { screen: "ringing", video: "offer" };
}

interface VideoRecording {
  recording_id: string;
  session_id?: string;
  started_at_utc?: string;
  duration_s?: number | null;
  path: string | null;
  consent_context: "visitor_initiated" | "bell_event" | null;
  thumbnail_path: string | null;
  playback_url?: string;
}

function AdminVideoMessagePlayer({
  recordingId,
  token,
}: {
  recordingId: string;
  token: string;
}) {
  const [playbackUrl, setPlaybackUrl] = useState<string | null>(null);
  const [playerState, setPlayerState] = useState<"idle" | "loading" | "unavailable">("idle");

  useEffect(
    () => () => {
      if (playbackUrl) URL.revokeObjectURL(playbackUrl);
    },
    [playbackUrl]
  );

  const load = async () => {
    setPlayerState("loading");
    try {
      const response = await fetch(`${API_BASE}/admin/media-inbox/${recordingId}/file`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error("playback unavailable");
      const nextUrl = URL.createObjectURL(await response.blob());
      setPlaybackUrl((previous) => {
        if (previous) URL.revokeObjectURL(previous);
        return nextUrl;
      });
      setPlayerState("idle");
    } catch {
      setPlayerState("unavailable");
    }
  };

  if (playbackUrl) {
    return <video className="admin-message-video" src={playbackUrl} controls playsInline />;
  }
  return (
    <BigButton onClick={load} disabled={playerState === "loading"}>
      {playerState === "loading" ? "Loading…" : playerState === "unavailable" ? "Retry playback" : "Play message"}
    </BigButton>
  );
}

interface PhotoReview {
  recording_id: string;
  session_id: string;
  review_url: string;
  size_bytes: number;
  sha256: string;
}

interface GalleryPhoto extends Recording {
  gallery_status: "pending" | "approved" | "deleted";
  tags: string[];
  wallboard_moment: boolean;
}

interface WallboardMoment {
  recording_id: string;
  tags: string[];
  approved_at: string | null;
  thumbnail_path: string | null;
}

const VISITOR_STATES: SessionState[] = [
  "BUTTON_PRESSED",
  "VISITOR_MODE",
  "RINGING",
  "ANSWERED",
  "UNANSWERED_TIMEOUT",
  "VIDEO_MESSAGE_OFFERED",
  "VIDEO_MESSAGE_RECORDING",
  "VIDEO_MESSAGE_REVIEW",
  "VIDEO_MESSAGE_SAVED",
];

interface Recording {
  recording_id: string;
  session_id: string;
  kind: string;
  stream: string;
  started_at_utc: string;
  finalized_at_utc: string | null;
  path: string | null;
  duration_s: number | null;
  size_bytes: number | null;
  sha256: string | null;
  consent_context: string | null;
  thumbnail_path: string | null;
  sync_status: string;
}
// Wallboard keeps rendering its takeover view through SESSION_END so the
// thank-you screen is visible before the session auto-expires to IDLE.
const WALLBOARD_TAKEOVER_STATES: SessionState[] = [...VISITOR_STATES, "SESSION_END"];

export function App() {
  const [route, setRoute] = useState<string>(window.location.pathname);
  const [sessionState, setSessionState] = useState<SessionState>("IDLE");
  const [activeProfile, setActiveProfile] = useState<string | null>(null);
  const [activeDisplayName, setActiveDisplayName] = useState<string | null>(null);
  const [mockSessionId, setMockSessionId] = useState<string>(() => crypto.randomUUID());
  const [showSimPanel, setShowSimPanel] = useState<boolean>(DEV_TOOLS_ENABLED);
  const [currentTime, setCurrentTime] = useState<Date>(new Date());
  const [wallboardFocusRequest, setWallboardFocusRequest] = useState<WallboardFocusRequest | null>(null);
  const [wallboardLaunchStatus, setWallboardLaunchStatus] = useState<string>("Ambient grid");
  const [eventConnection, setEventConnection] = useState<"connecting" | "connected" | "disconnected">(
    "connecting"
  );
  const [ringRequestState, setRingRequestState] = useState<"idle" | "sending" | "sent" | "failed">(
    "idle"
  );
  
  // DoorPad local state
  const [doorPadScreen, setDoorPadScreen] = useState<DoorPadScreen>("home");
  const [videoStep, setVideoStep] = useState<VideoStep>("offer");
  const [photoStep, setPhotoStep] = useState<PhotoStep>("offer");
  const [countdown, setCountdown] = useState<number>(3);
  const [photoCountdown, setPhotoCountdown] = useState<number>(3);
  const [recordingElapsed, setRecordingElapsed] = useState<number>(0);
  const [maxRecordingS, setMaxRecordingS] = useState<number>(60);
  const [latestRecording, setLatestRecording] = useState<VideoRecording | null>(null);
  const [currentPhoto, setCurrentPhoto] = useState<PhotoReview | null>(null);
  // Post-bell photo check-in (auto-captured on the "ringing" screen). Kept
  // separate from the manual photo-booth `currentPhoto` so the two flows never
  // clobber each other's save/discard bookkeeping.
  const [postRingPhoto, setPostRingPhoto] = useState<PhotoReview | null>(null);
  const [postRingPhotoStatus, setPostRingPhotoStatus] = useState<PostRingPhotoStatus>("idle");
  const [postRingName, setPostRingName] = useState<string>("");
  const [postRingCheckinPending, setPostRingCheckinPending] = useState<boolean>(false);
  const [visitorQrUrl, setVisitorQrUrl] = useState<string | null>(null);
  const [mediaActionPending, setMediaActionPending] = useState<boolean>(false);
  const [adminRecordings, setAdminRecordings] = useState<VideoRecording[]>([]);
  const [adminInboxState, setAdminInboxState] = useState<"idle" | "loading" | "ready" | "unavailable">(
    "idle"
  );
  const [adminToken, setAdminToken] = useState<string>(
    () => window.localStorage.getItem(ADMIN_TOKEN_KEY) ?? ""
  );
  const [adminTokenDraft, setAdminTokenDraft] = useState<string>("");
  const [sessionObservedAt, setSessionObservedAt] = useState<number>(() => Date.now());
  const [galleryPhotos, setGalleryPhotos] = useState<GalleryPhoto[]>([]);
  const [wallboardMoments, setWallboardMoments] = useState<WallboardMoment[]>([]);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Admin surface media & storage states
  const [storageStatus, setStorageStatus] = useState({
    free_bytes: 0,
    queue_depth: 0,
    oldest_unsynced_s: 0,
    recording_allowed: true,
  });
  const [storageStatusKnown, setStorageStatusKnown] = useState(false);

  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [kindFilter, setKindFilter] = useState<string>("all");
  const [syncFilter, setSyncFilter] = useState<string>("all");
  const [pageSize, setPageSize] = useState<number>(5);
  const [currentCursor, setCurrentCursor] = useState<string>("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const [loading, setLoading] = useState<boolean>(false);

  // Social feature state (T-403)
  const [guestbookText, setGuestbookText] = useState<string>("");
  const [selectedGuestbookPhrase, setSelectedGuestbookPhrase] = useState<string | null>(null);
  const [guestbookKeyboardOpen, setGuestbookKeyboardOpen] = useState(false);
  const [guestbookSubmitting, setGuestbookSubmitting] = useState<boolean>(false);
  const [currentPoll, setCurrentPoll] = useState<Poll | null>(null);
  const [pollResults, setPollResults] = useState<PollResultRow[] | null>(null);
  const [pollVoteError, setPollVoteError] = useState<string | null>(null);
  const [selectedPollOptionId, setSelectedPollOptionId] = useState<string | null>(null);
  const [doorPadVotedOptionId, setDoorPadVotedOptionId] = useState<string | null>(null);
  const [checkinSubmitting, setCheckinSubmitting] = useState<boolean>(false);
  const [myContent, setMyContent] = useState<MyContentRef[]>([]);
  const [approvedGuestbook, setApprovedGuestbook] = useState<GuestbookEntry[]>([]);
  const [guestbookAmbientState, setGuestbookAmbientState] = useState<
    "idle" | "ready" | "unavailable"
  >("idle");
  const [pollAmbientState, setPollAmbientState] = useState<"idle" | "ready" | "unavailable">(
    "idle"
  );

  const [presenceState, setPresenceState] = useState<
    Record<string, { label: PresenceLabel; occurredAt: string }>
  >(() =>
    MOCK_AMBIENT_ENABLED
      ? {
          resident_1: {
            label: presenceFixture.owner.label,
            occurredAt: presenceFixture.owner.occurred_at,
          },
          resident_2: {
            label: presenceFixture.roommate.label,
            occurredAt: presenceFixture.roommate.occurred_at,
          },
        }
      : ({} as Record<string, { label: PresenceLabel; occurredAt: string }>)
  );
  const [birdSummary, setBirdSummary] = useState<TimedAmbient<AmbientBirdSummaryPayload> | null>(
    () =>
      MOCK_AMBIENT_ENABLED
        ? {
            occurredAt: birdFixture.occurred_at,
            payload: {
              window: "today",
              top_species: birdFixture.top_species,
              total_detections: birdFixture.total_detections,
            },
          }
        : null
  );
  const [aircraftSummary, setAircraftSummary] = useState<
    TimedAmbient<AmbientAircraftSummaryPayload> | null
  >(() =>
    MOCK_AMBIENT_ENABLED
      ? {
          occurredAt: aircraftFixture.occurred_at,
          payload: { nearby: aircraftFixture.nearby, as_of: aircraftFixture.occurred_at },
        }
      : null
  );
  const [satellitePass, setSatellitePass] = useState<TimedAmbient<AmbientSatellitePassPayload> | null>(
    () =>
      MOCK_AMBIENT_ENABLED
        ? {
            occurredAt: satelliteFixture.occurred_at,
            payload: {
              satellite: satelliteFixture.satellite,
              rise_at: satelliteFixture.rise_at,
              max_elevation_deg: satelliteFixture.max_elevation_deg,
              direction: satelliteFixture.direction,
              visible: satelliteFixture.visible,
            },
          }
        : null
  );
  const [printerStatus, setPrinterStatus] = useState<TimedAmbient<AmbientPrinterStatusPayload> | null>(
    () =>
      MOCK_AMBIENT_ENABLED
        ? {
            occurredAt: printerFixture.occurred_at,
            payload: {
              state: printerFixture.state as AmbientPrinterStatusPayload["state"],
              job_name: printerFixture.job_name,
              progress_pct: printerFixture.progress_pct,
              eta: printerFixture.eta,
            },
          }
        : null
  );
  const [foodRecommendation, setFoodRecommendation] = useState<
    TimedAmbient<AmbientFoodRecommendationPayload> | null
  >(() =>
    MOCK_AMBIENT_ENABLED
      ? {
          occurredAt: foodFixture.occurred_at,
          payload: {
            date: foodFixture.occurred_at.slice(0, 10),
            title: foodFixture.title,
            detail: foodFixture.detail,
            provider: foodFixture.provider,
          },
        }
        : null
  );
  const [moodUpdate, setMoodUpdate] = useState<TimedAmbient<{ mood: string }> | null>(() =>
    MOCK_AMBIENT_ENABLED
      ? { occurredAt: moodFixture.occurred_at, payload: { mood: moodFixture.mood } }
      : null
  );
  const [scoreboardEntries, setScoreboardEntries] = useState<Record<string, ScoreboardEntry>>(() =>
    MOCK_AMBIENT_ENABLED
      ? Object.fromEntries(
          scoreboardFixture.scores.map((entry, index) => [
            `fixture-${index}`,
            { score: entry.score, occurredAt: scoreboardFixture.occurred_at },
          ])
        )
      : {}
  );
  const [ambientAlert, setAmbientAlert] = useState<AmbientAlert | null>(null);

  const clientRef = useRef<DoorboardEventClient | null>(null);
  const doorPadFocusRef = useRef<HTMLDivElement | null>(null);
  const alertTimeoutRef = useRef<number | null>(null);
  const latestAlertPriorityRef = useRef<number>(0);
  const lastBirdTotalRef = useRef<number | null>(null);
  const lastPrinterStateRef = useRef<string | null>(null);
  const lastAmbientAlertsRef = useRef<
    Partial<Record<AmbientAlert["kind"], { key: string; shownAt: number }>>
  >({});

  const applySessionSnapshot = useCallback(
    (snapshot: DoorApiSnapshot["session"] | null | undefined) => {
      const nextState = snapshot?.state;
      if (!nextState) return;
      setSessionState((previous) => {
        if (previous !== nextState) setSessionObservedAt(Date.now());
        return nextState;
      });
      if (snapshot?.session_id) setMockSessionId(snapshot.session_id);
      if (snapshot?.display_name !== undefined) setActiveDisplayName(snapshot.display_name);
      if (snapshot?.profile_id !== undefined) setActiveProfile(snapshot.profile_id);
      const target = doorPadRouteForState(nextState);
      setDoorPadScreen(target.screen);
      setVideoStep(target.video);
      if (nextState === "IDLE") {
        setActiveDisplayName(null);
        setActiveProfile(null);
        setRingRequestState("idle");
        // Fresh session: clear any post-bell check-in photo state.
        setPostRingPhoto(null);
        setPostRingPhotoStatus("idle");
        setPostRingName("");
        setPostRingCheckinPending(false);
      }
    },
    []
  );

  const showAmbientAlert = useCallback((alert: AmbientAlert) => {
    const key = `${alert.title}|${alert.detail}`;
    const previous = lastAmbientAlertsRef.current[alert.kind];
    const now = Date.now();
    if (previous?.key === key && now - previous.shownAt < 60_000) return;
    if (alert.priority < latestAlertPriorityRef.current) return;
    lastAmbientAlertsRef.current[alert.kind] = { key, shownAt: now };
    latestAlertPriorityRef.current = alert.priority;
    setAmbientAlert(alert);
    if (alertTimeoutRef.current !== null) window.clearTimeout(alertTimeoutRef.current);
    alertTimeoutRef.current = window.setTimeout(() => {
      latestAlertPriorityRef.current = 0;
      setAmbientAlert(null);
      alertTimeoutRef.current = null;
    }, alert.kind === "aircraft" ? 12000 : 8000);
  }, []);

  // Sync pathname route
  useEffect(() => {
    const handlePopState = () => {
      setRoute(window.location.pathname);
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  // Update clock every second
  useEffect(() => {
    const interval = setInterval(() => {
      setCurrentTime(new Date());
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (sessionState === "IDLE") {
      saveMyContent(mockSessionId, []);
      setMyContent([]);
      socialApi.clearVisitorToken();
      return;
    }
    setMyContent(loadMyContent(mockSessionId));
  }, [mockSessionId, sessionState]);

  useEffect(() => {
    if (route !== "/doorpad") return undefined;
    const frame = window.requestAnimationFrame(() => doorPadFocusRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [doorPadScreen, route, videoStep]);

  useEffect(() => {
    if (!WALLBOARD_TAKEOVER_STATES.includes(sessionState)) return;
    setAmbientAlert(null);
    latestAlertPriorityRef.current = 0;
    if (alertTimeoutRef.current !== null) {
      window.clearTimeout(alertTimeoutRef.current);
      alertTimeoutRef.current = null;
    }
  }, [sessionState]);

  useEffect(
    () => () => {
      if (alertTimeoutRef.current !== null) window.clearTimeout(alertTimeoutRef.current);
    },
    []
  );

  useEffect(() => {
    const readStoredFocus = () => {
      try {
        const raw = window.localStorage.getItem(WALLBOARD_CONTROL_STORAGE_KEY);
        if (!raw) return;
        const parsed = JSON.parse(raw) as unknown;
        if (isWallboardFocusRequest(parsed)) {
          setWallboardFocusRequest(parsed);
          setWallboardLaunchStatus(
            parsed.mode === "ambient" ? "Ambient grid" : `Focused: ${parsed.channel}`
          );
        }
      } catch {
        setWallboardFocusRequest(null);
      }
    };

    readStoredFocus();

    const handleStorage = (event: StorageEvent) => {
      if (event.key === WALLBOARD_CONTROL_STORAGE_KEY) readStoredFocus();
    };
    const handleLocalFocus = (event: Event) => {
      const detail = (event as CustomEvent<unknown>).detail;
      if (isWallboardFocusRequest(detail)) {
        setWallboardFocusRequest(detail);
      }
    };

    window.addEventListener("storage", handleStorage);
    window.addEventListener(WALLBOARD_CONTROL_EVENT, handleLocalFocus);
    return () => {
      window.removeEventListener("storage", handleStorage);
      window.removeEventListener(WALLBOARD_CONTROL_EVENT, handleLocalFocus);
    };
  }, []);

  useEffect(() => {
    if (wallboardFocusRequest?.mode !== "focus" || !wallboardFocusRequest.expiresAt) return undefined;
    const remainingMs = Math.max(0, wallboardFocusRequest.expiresAt - Date.now());
    const timeout = window.setTimeout(() => setWallboardFocusRequest(null), remainingMs);
    return () => window.clearTimeout(timeout);
  }, [wallboardFocusRequest]);

  // Initialize event client
  useEffect(() => {
    const client = new DoorboardEventClient({
      // Connect to simulator ws, or fallback to mock BroadcastChannel
      wsUrl: wsUrlFromApiBase(API_BASE),
      filters: ["session.*", "vision.*", "door.*", "media.*", "ambient.*", "status.*", "social.*"],
      onStatusChange: setEventConnection,
      onSnapshot: (snapshot) => {
        const value = snapshot as DoorApiSnapshot["session"] | DoorApiSnapshot;
        const session =
          value && typeof value === "object" && "session" in value
            ? (value as DoorApiSnapshot).session
            : (value as DoorApiSnapshot["session"]);
        applySessionSnapshot(session);
      },
    });

    clientRef.current = client;

    // Listen to session state changes
    const unsubscribeSession = client.subscribe("session.state_changed", (event: DoorboardEvent) => {
      if (event && event.type === "session.state_changed" && event.payload) {
        const toState = event.payload.to_state;
        applySessionSnapshot({ state: toState, session_id: event.payload.session_id });
      }
    });

    // Listen to vision identity stable events
    const unsubscribeVision = client.subscribe("vision.identity_stable", (event: DoorboardEvent) => {
      if (event && event.type === "vision.identity_stable" && event.payload) {
        setActiveProfile(event.payload.profile_id);
        setActiveDisplayName(event.payload.display_name);
      }
    });

    // Listen to media storage status events
    const unsubscribeMedia = client.subscribe("media.storage_status", (event: DoorboardEvent) => {
      if (event && event.type === "media.storage_status" && event.payload) {
        setStorageStatus({
          free_bytes: event.payload.free_bytes,
          queue_depth: event.payload.queue_depth,
          oldest_unsynced_s: event.payload.oldest_unsynced_s,
          recording_allowed: event.payload.recording_allowed,
        });
        setStorageStatusKnown(true);
      }
    });

    const unsubscribePresence = client.subscribe("status.presence_changed", (event: DoorboardEvent) => {
      if (event.type !== "status.presence_changed") return;
      const subjectKey = safeDisplayText(event.payload.subject_id, 128);
      if (!subjectKey || subjectKey === "__proto__" || subjectKey === "constructor") return;
      setPresenceState((previous) => {
        const next = { ...previous };
        if (!(subjectKey in next) && Object.keys(next).length >= MAX_PRESENCE_SUBJECTS) {
          const oldest = Object.entries(next).sort(
            ([, left], [, right]) => Date.parse(left.occurredAt) - Date.parse(right.occurredAt)
          )[0];
          if (oldest) delete next[oldest[0]];
        }
        next[subjectKey] = {
          label: event.payload.label,
          occurredAt: event.occurred_at,
        };
        return next;
      });
    });

    const unsubscribeBirds = client.subscribe("ambient.bird_summary", (event: DoorboardEvent) => {
      if (event.type !== "ambient.bird_summary") return;
      const previousTotal = lastBirdTotalRef.current;
      lastBirdTotalRef.current = event.payload.total_detections;
      setBirdSummary({ payload: event.payload, occurredAt: event.occurred_at });
      if (
        event.payload.top_species.length > 0 &&
        (previousTotal === null || event.payload.total_detections > previousTotal)
      ) {
        const species = event.payload.top_species[0];
        showAmbientAlert({
          id: event.event_id,
          kind: "bird",
          title: `New in the latest bird update: ${safeDisplayText(species.name) || "Bird detected"}`,
          detail: `${species.count} today · ${(species.confidence_avg * 100).toFixed(0)}% average confidence`,
          priority: 1,
        });
      }
    });

    const unsubscribeAircraft = client.subscribe("ambient.aircraft_summary", (event: DoorboardEvent) => {
      if (event.type !== "ambient.aircraft_summary") return;
      setAircraftSummary({ payload: event.payload, occurredAt: event.payload.as_of });
      const closest = event.payload.nearby
        .filter((aircraft) => isFiniteNonNegative(aircraft.distance_km))
        .sort((a, b) => a.distance_km - b.distance_km)[0];
      if (closest && closest.distance_km <= AIRCRAFT_ALERT_DISTANCE_KM) {
        showAmbientAlert({
          id: event.event_id,
          kind: "aircraft",
          title: `${safeDisplayText(closest.callsign, 16) || "Aircraft"} is overhead`,
          detail: `${isFiniteNonNegative(closest.altitude_ft) ? closest.altitude_ft.toLocaleString() : "Unknown"} ft · ${closest.distance_km.toFixed(1)} km away · heading ${Number.isFinite(closest.heading) ? Math.round(closest.heading) : "unknown"}°`,
          priority: 4,
        });
      }
    });

    const unsubscribeSatellite = client.subscribe("ambient.satellite_pass", (event: DoorboardEvent) => {
      if (event.type !== "ambient.satellite_pass") return;
      setSatellitePass({ payload: event.payload, occurredAt: event.occurred_at });
      const minutesUntilRise = (Date.parse(event.payload.rise_at) - Date.now()) / 60000;
      if (event.payload.visible && minutesUntilRise >= 0 && minutesUntilRise <= 15) {
        showAmbientAlert({
          id: event.event_id,
          kind: "satellite",
          title: `${safeDisplayText(event.payload.satellite) || "Satellite"} rises soon`,
          detail: `${Math.max(1, Math.ceil(minutesUntilRise))} min · ${event.payload.direction} · ${event.payload.max_elevation_deg.toFixed(0)}° max elevation`,
          priority: 3,
        });
      }
    });

    const unsubscribePrinter = client.subscribe("ambient.printer_status", (event: DoorboardEvent) => {
      if (event.type !== "ambient.printer_status") return;
      const previousState = lastPrinterStateRef.current;
      lastPrinterStateRef.current = event.payload.state;
      setPrinterStatus({ payload: event.payload, occurredAt: event.occurred_at });
      if (event.payload.state === "error" || (previousState === "printing" && event.payload.state === "idle")) {
        showAmbientAlert({
          id: event.event_id,
          kind: "printer",
          title: event.payload.state === "error" ? "3D printer needs attention" : "3D print finished",
          detail: event.payload.job_name
            ? safeDisplayText(event.payload.job_name)
            : "No job name provided",
          priority: event.payload.state === "error" ? 3 : 2,
        });
      }
    });

    const unsubscribeFood = client.subscribe("ambient.food_recommendation", (event: DoorboardEvent) => {
      if (event.type !== "ambient.food_recommendation") return;
      setFoodRecommendation({ payload: event.payload, occurredAt: event.occurred_at });
    });

    const unsubscribeMood = client.subscribe("social.mood_updated", (event: DoorboardEvent) => {
      if (event.type !== "social.mood_updated") return;
      setMoodUpdate({
        payload: { mood: safeDisplayText(event.payload.mood, 40) },
        occurredAt: event.occurred_at,
      });
    });

    const unsubscribeScoreboard = client.subscribe(
      "social.scoreboard_updated",
      (event: DoorboardEvent) => {
        if (event.type !== "social.scoreboard_updated" || !Number.isFinite(event.payload.delta)) return;
        const entryKey = safeDisplayText(event.payload.entry_id, 128);
        if (!entryKey || entryKey === "__proto__" || entryKey === "constructor") return;
        setScoreboardEntries((previous) => {
          const next = { ...previous };
          if (!(entryKey in next) && Object.keys(next).length >= MAX_SCOREBOARD_ENTRIES) {
            const oldest = Object.entries(next).sort(
              ([, left], [, right]) => Date.parse(left.occurredAt) - Date.parse(right.occurredAt)
            )[0];
            if (oldest) delete next[oldest[0]];
          }
          const nextScore = (next[entryKey]?.score ?? 0) + Math.trunc(event.payload.delta);
          next[entryKey] = {
            score: Math.min(Number.MAX_SAFE_INTEGER, Math.max(Number.MIN_SAFE_INTEGER, nextScore)),
            occurredAt: event.occurred_at,
          };
          return next;
        });
      }
    );

    return () => {
      unsubscribeSession();
      unsubscribeVision();
      unsubscribeMedia();
      unsubscribePresence();
      unsubscribeBirds();
      unsubscribeAircraft();
      unsubscribeSatellite();
      unsubscribePrinter();
      unsubscribeFood();
      unsubscribeMood();
      unsubscribeScoreboard();
      client.close();
    };
  }, [applySessionSnapshot, showAmbientAlert]);

  useEffect(() => {
    if (!WALLBOARD_TAKEOVER_STATES.includes(sessionState)) {
      if (sessionState === "IDLE") setVisitorQrUrl(null);
      return undefined;
    }
    let cancelled = false;
    void fetch(`${API_BASE}/visitor-token`)
      .then(async (response) => {
        if (!response.ok) throw new Error("visitor token unavailable");
        return response.json() as Promise<{ token: string; url: string }>;
      })
      .then((data) => {
        if (cancelled) return;
        socialApi.setVisitorToken(data.token);
        setVisitorQrUrl(data.url);
      })
      .catch(() => {
        if (!cancelled) setVisitorQrUrl(null);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionState]);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE}/session`, { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : null))
      .then((data: DoorApiSnapshot | null) => {
        applySessionSnapshot(data?.session);
        if (typeof data?.config?.max_recording_s === "number") {
          setMaxRecordingS(data.config.max_recording_s);
        }
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [applySessionSnapshot]);

  useEffect(() => {
    if (route !== "/admin" && route !== "/diagnostics") return undefined;
    if (!adminToken) {
      setAdminInboxState("idle");
      setAdminRecordings([]);
      return undefined;
    }
    const controller = new AbortController();
    setAdminInboxState("loading");
    fetch(`${API_BASE}/admin/media-inbox`, {
      signal: controller.signal,
      headers: adminHeaders(),
    })
      .then((response) => {
        if (!response.ok) throw new Error(`admin inbox unavailable: ${response.status}`);
        return response.json();
      })
      .then((data: { recordings?: VideoRecording[] }) => {
        setAdminRecordings(data.recordings ?? []);
        setAdminInboxState("ready");
      })
      .catch(() => {
        if (!controller.signal.aborted) setAdminInboxState("unavailable");
      });
    return () => controller.abort();
  }, [adminToken, route]);

  useEffect(() => {
    if (
      !FEATURE_PHOTOBOOTH ||
      !adminToken ||
      (route !== "/admin" && route !== "/diagnostics")
    ) return undefined;
    const controller = new AbortController();
    fetch(`${API_BASE}/admin/gallery/photos`, {
      signal: controller.signal,
      headers: adminHeaders(),
    })
      .then((response) => (response.ok ? response.json() : { photos: [] }))
      .then((data: { photos?: GalleryPhoto[] }) => {
        setGalleryPhotos(data.photos ?? []);
      })
      .catch(() => setGalleryPhotos([]));
    return () => controller.abort();
  }, [adminToken, route]);

  // Show temporary toast feedback
  const triggerToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 3000);
  };

  const navigateTo = (path: string) => {
    window.history.pushState(null, "", path);
    setRoute(path);
  };

  const returnDoorPadToContext = () => {
    if (VISITOR_STATES.includes(sessionState)) {
      setDoorPadScreen("ringing");
    } else {
      setDoorPadScreen("home");
    }
  };

  const setWallboardChannel = (channel: "ambient" | WallboardFocusChannel) => {
    const request = createWallboardFocusRequest(channel);
    setWallboardFocusRequest(request);
    setWallboardLaunchStatus(channel === "ambient" ? "Ambient grid" : `Focused: ${channel}`);
    window.localStorage.setItem(WALLBOARD_CONTROL_STORAGE_KEY, JSON.stringify(request));
    window.dispatchEvent(new CustomEvent(WALLBOARD_CONTROL_EVENT, { detail: request }));
  };

  const returnWallboardAmbient = () => {
    setWallboardChannel("ambient");
  };

  const MEDIA_API_BASE = window.location.port === "5173" ? "http://127.0.0.1:8082" : "";

  const fetchRecordings = useCallback(async (cursorVal = currentCursor) => {
    setLoading(true);
    try {
      let url = `${API_BASE}/admin/recordings?limit=${pageSize}`;
      if (kindFilter !== "all") {
        url += `&kind=${kindFilter}`;
      }
      if (syncFilter !== "all") {
        url += `&sync_status=${syncFilter}`;
      }
      if (cursorVal) {
        url += `&cursor=${encodeURIComponent(cursorVal)}`;
      }
      
      const res = await fetch(url, { headers: adminHeaders() });
      if (res.ok) {
        const data = await res.json();
        setRecordings(data.recordings || []);
        setNextCursor(data.next_cursor || null);
      } else {
        console.error("Failed to fetch recordings", res.status);
      }
    } catch (err) {
      console.error("Error fetching recordings:", err);
    } finally {
      setLoading(false);
    }
  }, [currentCursor, pageSize, kindFilter, syncFilter]);

  useEffect(() => {
    if (adminToken && (route === "/admin" || route === "/diagnostics")) {
      fetchRecordings(currentCursor);
    }
  }, [adminToken, route, currentCursor, fetchRecordings]);

  const handleNextPage = () => {
    if (nextCursor) {
      setCursorStack((prev) => [...prev, currentCursor]);
      setCurrentCursor(nextCursor);
    }
  };

  const handlePrevPage = () => {
    if (cursorStack.length > 0) {
      const prevStack = [...cursorStack];
      const prevCursor = prevStack.pop() || "";
      setCursorStack(prevStack);
      setCurrentCursor(prevCursor);
    }
  };

  const handleDeleteRecording = async (recordingId: string) => {
    if (!window.confirm("Are you sure you want to delete this recording?")) {
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/admin/recordings/${recordingId}`, {
        method: "DELETE",
        headers: adminHeaders(),
      });
      if (res.ok) {
        triggerToast("Recording deleted");
        setAdminRecordings((previous) =>
          previous.filter((recording) => recording.recording_id !== recordingId)
        );
        // Reset to first page or refresh current page
        fetchRecordings(currentCursor);
      } else {
        triggerToast("Failed to delete recording");
      }
    } catch (err) {
      console.error("Error deleting recording:", err);
      triggerToast("Error deleting recording");
    }
  };

  const refreshGalleryPhotos = useCallback(async () => {
    if (!FEATURE_PHOTOBOOTH) return;
    try {
      const response = await fetch(`${API_BASE}/admin/gallery/photos`, {
        headers: adminHeaders(),
      });
      const data = response.ok ? ((await response.json()) as { photos?: GalleryPhoto[] }) : {};
      setGalleryPhotos(data.photos ?? []);
    } catch {
      setGalleryPhotos([]);
    }
  }, []);

  const approveGalleryPhoto = async (photo: GalleryPhoto) => {
    const tagText = window.prompt("Tags, comma-separated", photo.tags.join(", "));
    if (tagText === null) return;
    try {
      const response = await fetch(`${API_BASE}/admin/gallery/photos/${photo.recording_id}/approve`, {
        method: "POST",
        headers: adminHeaders(true),
        body: JSON.stringify({
          tags: tagText.split(",").map((tag) => tag.trim()).filter(Boolean),
          wallboard_moment: photo.wallboard_moment,
        }),
      });
      triggerToast(response.ok ? "Photo approved" : "Couldn't approve photo");
      void refreshGalleryPhotos();
    } catch {
      triggerToast("Couldn't approve photo");
    }
  };

  const updateGalleryTags = async (photo: GalleryPhoto, wallboardMoment?: boolean) => {
    const tagText = window.prompt("Tags, comma-separated", photo.tags.join(", "));
    if (tagText === null && wallboardMoment === undefined) return;
    try {
      const response = await fetch(`${API_BASE}/admin/gallery/photos/${photo.recording_id}/tags`, {
        method: "PATCH",
        headers: adminHeaders(true),
        body: JSON.stringify({
          tags: tagText === null ? photo.tags : tagText.split(",").map((tag) => tag.trim()),
          wallboard_moment: wallboardMoment ?? photo.wallboard_moment,
        }),
      });
      triggerToast(response.ok ? "Photo updated" : "Couldn't update photo");
      void refreshGalleryPhotos();
    } catch {
      triggerToast("Couldn't update photo");
    }
  };

  const deleteGalleryPhoto = async (photo: GalleryPhoto) => {
    if (!window.confirm("Delete this photo from SSD, NAS, gallery, and thumbnails?")) return;
    try {
      const response = await fetch(`${API_BASE}/admin/gallery/photos/${photo.recording_id}`, {
        method: "DELETE",
        headers: adminHeaders(),
      });
      triggerToast(response.ok ? "Photo deleted" : "Couldn't delete photo");
      void refreshGalleryPhotos();
      void fetchRecordings(currentCursor);
    } catch {
      triggerToast("Couldn't delete photo");
    }
  };

  // Feed the ambient Guestbook Highlights + Room Poll tiles from real data.
  useEffect(() => {
    if (route !== "/wallboard") return;
    let cancelled = false;
    const load = () => {
      socialApi
        .listGuestbook(5)
        .then((entries) => {
          if (!cancelled) {
            setApprovedGuestbook(entries);
            setGuestbookAmbientState("ready");
          }
        })
        .catch(() => {
          if (!cancelled) setGuestbookAmbientState("unavailable");
        });
      socialApi
        .getCurrentPoll()
        .then((poll) => {
          if (cancelled) return;
          setCurrentPoll(poll);
          setPollAmbientState("ready");
          if (poll) {
            return socialApi.getPollResults(poll.id).then((results) => {
              if (!cancelled) setPollResults(results);
            });
          }
          setPollResults(null);
        })
        .catch(() => {
          if (!cancelled) setPollAmbientState("unavailable");
        });
      if (FEATURE_PHOTOBOOTH) {
        fetch(`${API_BASE}/wallboard/moments`)
          .then((response) => (response.ok ? response.json() : { photos: [] }))
          .then((data: { photos?: WallboardMoment[] }) => {
            if (!cancelled) setWallboardMoments(data.photos ?? []);
          })
          .catch(() => {
            // Private gallery unavailable — keep the last approved moment list.
          });
      }
      // NOTE: the visitor collage is intentionally NOT fetched or polled here.
      // It is private all year and only revealed on-demand via the owner-only
      // /reveal#<token> page — never on the public wallboard. See RevealPage.
    };
    load();
    const interval = setInterval(load, 30000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [route]);

  // Load the current poll + results whenever the DoorPad poll screen opens.
  useEffect(() => {
    if (doorPadScreen !== "poll") return;
    let cancelled = false;
    setSelectedPollOptionId(null);
    setDoorPadVotedOptionId(null);
    setPollVoteError(null);
    socialApi
      .getCurrentPoll()
      .then((poll) => {
        if (cancelled) return;
        setCurrentPoll(poll);
        if (poll) {
          return socialApi.getPollResults(poll.id).then((results) => {
            if (!cancelled) setPollResults(results);
          });
        }
        setPollResults(null);
      })
      .catch((err) => {
        if (!cancelled) setPollVoteError(apiErrorMessage(err, "Couldn't load the poll."));
      });
    return () => {
      cancelled = true;
    };
  }, [doorPadScreen]);

  const rememberMyContent = (ref: MyContentRef) => {
    setMyContent((previous) => {
      const next = [...previous.filter((item) => item.id !== ref.id), ref];
      saveMyContent(mockSessionId, next);
      return next;
    });
  };

  const handleGuestbookSubmit = async (text: string) => {
    if (guestbookSubmitting) return;
    setGuestbookSubmitting(true);
    try {
      const entry = await socialApi.createGuestbookEntry(text, null);
      rememberMyContent({ kind: "guestbook", id: entry.id, label: text.slice(0, 40) });
      triggerToast("Note submitted! It'll show up once approved.");
      setGuestbookText("");
      setSelectedGuestbookPhrase(null);
      returnDoorPadToContext();
    } catch (err) {
      triggerToast(apiErrorMessage(err, "Couldn't submit your note — try again."));
    } finally {
      setGuestbookSubmitting(false);
    }
  };

  const handlePollVote = async () => {
    if (!currentPoll || !selectedPollOptionId || doorPadVotedOptionId) return;
    setPollVoteError(null);
    try {
      await socialApi.castVote(currentPoll.id, selectedPollOptionId);
      setDoorPadVotedOptionId(selectedPollOptionId);
      const results = await socialApi.getPollResults(currentPoll.id);
      setPollResults(results);
      triggerToast("Vote cast!");
    } catch (err) {
      setPollVoteError(apiErrorMessage(err, "Couldn't cast your vote."));
    }
  };

  const handleCheckin = async (kind: "enrolled" | "guest") => {
    if (checkinSubmitting) return;
    setCheckinSubmitting(true);
    try {
      const label = kind === "enrolled" && activeDisplayName ? activeDisplayName : "Guest";
      // door-api derives attribution server-side from the session's cached
      // identity — this client never asserts a person_id.
      const checkin = await socialApi.createCheckin(label);
      rememberMyContent({ kind: "checkin", id: checkin.id, label });
      triggerToast(
        checkin.person_id
          ? "Recognized check-in saved."
          : `Checked in as ${label}`
      );
      returnDoorPadToContext();
    } catch (err) {
      triggerToast(apiErrorMessage(err, "Couldn't check in — try again."));
    } finally {
      setCheckinSubmitting(false);
    }
  };

  const handleDeletionRequest = async (item: MyContentRef) => {
    try {
      await socialApi.requestDeletion(item.kind, item.id);
      const next = myContent.filter((c) => c.id !== item.id);
      setMyContent(next);
      saveMyContent(mockSessionId, next);
      triggerToast("Deletion request honored.");
    } catch (err) {
      triggerToast(apiErrorMessage(err, "Couldn't process the deletion request."));
    }
  };

  const handleDeletionRequestAll = async () => {
    if (myContent.length === 0) {
      triggerToast("No session submissions to delete.");
      return;
    }
    const items = [...myContent];
    try {
      await Promise.all(items.map((item) => socialApi.requestDeletion(item.kind, item.id)));
      setMyContent([]);
      saveMyContent(mockSessionId, []);
      triggerToast("Deletion requests honored.");
      returnDoorPadToContext();
    } catch (err) {
      triggerToast(apiErrorMessage(err, "Couldn't process every deletion request."));
    }
  };

  const triggerEvent = (toState: SessionState, profileId: string | null = null) => {
    if (!clientRef.current) return;

    let currentSessionId = mockSessionId;
    if (toState !== "IDLE" && sessionState === "IDLE") {
      // Start a new mock session with a new UUID when transitioning from IDLE
      currentSessionId = crypto.randomUUID();
      setMockSessionId(currentSessionId);
    }

    const traceId = crypto.randomUUID();

    if (profileId) {
      const visionEvent: DoorboardEvent = {
        event_id: uuidv7(),
        type: "vision.identity_stable",
        source: "door-ui-mock",
        occurred_at: new Date().toISOString(),
        monotonic_ms: performance.now(),
        door_id: "primary",
        trace_id: traceId,
        payload: {
          person_id: profileId === "owner" ? "prs_taylor" : "prs_alex",
          display_name: profileId === "owner" ? "Taylor" : "Alex",
          confidence: 0.98,
          expires_at: new Date(Date.now() + 60000).toISOString(),
          expires_at_monotonic_ms: performance.now() + 60000,
          profile_id: profileId,
        },
      };
      clientRef.current.publish(visionEvent);
    }

    const event: DoorboardEvent = {
      event_id: uuidv7(),
      type: "session.state_changed",
      source: "door-ui-mock",
      occurred_at: new Date().toISOString(),
      monotonic_ms: performance.now(),
      door_id: "primary",
      trace_id: traceId,
      payload: {
        session_id: currentSessionId,
        from_state: sessionState,
        to_state: toState,
        trigger: profileId ? "proactive_recognition" : "physical_bell",
      },
    };

    clientRef.current.publish(event);
  };

  const endVisitorSession = async () => {
    const previous = sessionState;
    applySessionSnapshot({ state: "SESSION_END", session_id: mockSessionId });
    const result = await postDoorApi("/doorpad/session/end");
    if (result?.session?.state) {
      applySessionSnapshot(result.session);
    } else {
      applySessionSnapshot({ state: previous, session_id: mockSessionId });
    }
  };

  // Auto-reset is a real Pi-local session action. It never relies on sending a
  // client-authored contract event over the server's broadcast-only socket.
  const handleReset = () => {
    void endVisitorSession();
  };

  // Render Simulator panel overlay (for interactive dev)
  const renderSimPanel = () => {
    if (!DEV_TOOLS_ENABLED) return null;
    if (!showSimPanel) {
      return (
        <button className="sim-panel-toggle-btn" onClick={() => setShowSimPanel(true)}>
          Open Sim Control
        </button>
      );
    }

    return (
      <div className="sim-panel-overlay">
        <div className="sim-panel-header">
          <h3>Simulation Panel</h3>
          <button onClick={() => setShowSimPanel(false)}>Close</button>
        </div>
        <div className="sim-panel-body">
          <p>Current Session State: <strong>{sessionState}</strong></p>
          <div className="sim-panel-buttons">
            <button onClick={() => triggerEvent("VISITOR_MODE", null)}>Press Bell (Generic)</button>
            <button onClick={() => triggerEvent("VISITOR_MODE", "owner")}>Recognize Owner</button>
            <button onClick={() => triggerEvent("VISITOR_MODE", "roommate")}>Recognize Roommate</button>
            <button onClick={() => handleReset()}>Reset to IDLE</button>
          </div>
        </div>
      </div>
    );
  };

  // Toast Component
  const renderToast = () => {
    if (!toastMessage) return null;
    return <div className="db-toast-message" role="status" aria-live="polite">{toastMessage}</div>;
  };

  // --- WALLBOARD SURFACE ---
  const renderWallboard = () => {
    const isVisitorMode = WALLBOARD_TAKEOVER_STATES.includes(sessionState);
    const presenceEntries = Object.values(presenceState);
    const scoreboardRows = Object.values(scoreboardEntries).sort((left, right) => right.score - left.score);
    const visitorPresence = {
      owner: { label: presenceEntries[0]?.label ?? ("unknown" as const) },
      roommate: { label: presenceEntries[1]?.label ?? ("unknown" as const) },
    };
    const focusedChannel =
      !isVisitorMode &&
      wallboardFocusRequest?.mode === "focus" &&
      wallboardFocusRequest.channel &&
      (!wallboardFocusRequest.expiresAt || wallboardFocusRequest.expiresAt > Date.now())
        ? wallboardFocusRequest.channel
        : null;

    return (
      <CrossfadeSwitch activeKey={isVisitorMode ? "visitor" : focusedChannel ?? "ambient"}>
        {isVisitorMode ? (
          <WallboardVisitorMode
            sessionState={sessionState}
            sessionId={mockSessionId}
            profileId={activeProfile}
            displayName={activeDisplayName}
            presence={visitorPresence}
            pollQuestion={currentPoll?.question ?? "No poll running right now."}
            visitorUrl={visitorQrUrl}
            onDone={endVisitorSession}
          />
        ) : focusedChannel ? (
          <WallboardFocusedView
            channel={focusedChannel}
            poll={currentPoll}
            pollResults={pollResults}
            guestbookEntries={approvedGuestbook}
            moments={wallboardMoments}
            ambient={{
              aircraft: aircraftSummary?.payload ?? null,
              birds: birdSummary?.payload ?? null,
              birdCollageUrl: BIRD_COLLAGE_URL,
              satellite: satellitePass?.payload ?? null,
              printer: printerStatus?.payload ?? null,
              food: foodRecommendation?.payload ?? null,
              scoreboard: scoreboardRows.length > 0 ? scoreboardRows : null,
            }}
            onReturnAmbient={returnWallboardAmbient}
          />
        ) : (
          // AMBIENT MODE - TILE DASHBOARD
          <div className="wallboard-ambient-view db-app-theme">
            <header className="ambient-header">
              <div className="ambient-header-left">
                <h1 className="ambient-header-title">Room {ROOM_LABEL} Wallboard</h1>
                <span className="ambient-header-subtitle">
                  {RESIDENTS_LABEL ? `${RESIDENTS_LABEL} · ` : ""}Dorm Hallway Display · {eventConnection === "connected" ? "Live updates" : "Using last-known data"}
                </span>
              </div>
              <div className="ambient-clock">
                {currentTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
              </div>
            </header>

            {ambientAlert && (
              <aside
                className={`ambient-alert ambient-alert--${ambientAlert.kind}`}
                role="status"
                aria-live="polite"
              >
                <p className="surface-eyebrow">{ambientAlert.kind}</p>
                <strong>{ambientAlert.title}</strong>
                <span>{ambientAlert.detail}</span>
              </aside>
            )}

            <main className="ambient-grid">
              {/* Tile 1: Presence */}
              <Tile title="Presence" asOf={presenceEntries[0]?.occurredAt ?? null} staleAfterMs={15 * 60 * 1000}>
                <div className="presence-tile-content">
                  {presenceEntries.slice(0, 2).map((presence, index) => (
                    <div className="presence-row" key={index}>
                      <span>Resident {index + 1}:</span>
                      <StatusBadge label={presence.label} />
                    </div>
                  ))}
                  {presenceEntries.length === 0 && <p>Presence unavailable.</p>}
                </div>
              </Tile>

              {/* Tile 2: Mood */}
              <Tile title="Current Mood" asOf={moodUpdate?.occurredAt ?? null} staleAfterMs={12 * 60 * 60 * 1000}>
                <div className="mood-tile-content">
                  {moodUpdate ? (
                    <>
                      <span className="mood-emoji" aria-hidden="true">Status</span>
                      <span className="mood-text">Resident mood: <strong>{moodUpdate.payload.mood || "Not shared"}</strong></span>
                    </>
                  ) : <span className="mood-text">No mood update available.</span>}
                </div>
              </Tile>

              {/* Tile 3: Birds */}
              <Tile title="Bird Detections" asOf={birdSummary?.occurredAt ?? null} staleAfterMs={60 * 60 * 1000}>
                <div className="bird-tile-content">
                  {birdSummary && <p className="bird-stat">Total today: <strong>{birdSummary.payload.total_detections}</strong></p>}
                  {birdSummary?.payload.top_species.slice(0, 5).map((s, idx) => (
                    <div key={idx} className="bird-row">
                      <span>{safeDisplayText(s.name) || "Unknown bird"} (x{s.count})</span>
                      <span className="bird-conf">{(s.confidence_avg * 100).toFixed(0)}% conf</span>
                    </div>
                  ))}
                  {!birdSummary && <p>Bird summary unavailable.</p>}
                  {birdSummary && birdSummary.payload.top_species.length === 0 && <p>No detections yet today.</p>}
                  {BIRD_COLLAGE_URL && (
                    <img
                      className="bird-collage"
                      src={BIRD_COLLAGE_URL}
                      alt="Live bird collage from the window feeder"
                      loading="lazy"
                      onError={(e) => {
                        e.currentTarget.style.display = "none";
                      }}
                    />
                  )}
                </div>
              </Tile>

              {/* Tile 4: Aircraft */}
              <Tile title="Overhead Aircraft" asOf={aircraftSummary?.occurredAt ?? null} staleAfterMs={5 * 60 * 1000}>
                <div className="aircraft-tile-content">
                  {aircraftSummary?.payload.nearby.slice(0, 5).map((a, idx) => (
                    <div key={idx} className="aircraft-row">
                      <span className="aircraft-call">{safeDisplayText(a.callsign, 16) || "Aircraft"}</span>
                      <span>{a.altitude_ft.toLocaleString()} ft</span>
                      <span>{a.distance_km} km away</span>
                    </div>
                  ))}
                  {!aircraftSummary && <p>Aircraft data unavailable.</p>}
                  {aircraftSummary && aircraftSummary.payload.nearby.length === 0 && <p>No nearby aircraft.</p>}
                </div>
              </Tile>

              {/* Tile 5: Satellite Pass */}
              <Tile title="Next Satellite Pass" asOf={satellitePass?.occurredAt ?? null} staleAfterMs={24 * 60 * 60 * 1000}>
                <div className="satellite-tile-content">
                  {satellitePass ? (
                    <>
                      <p><strong>{satellitePass.payload.satellite}</strong></p>
                      <p>Rise: {new Date(satellitePass.payload.rise_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</p>
                      <p>Direction: {satellitePass.payload.direction} ({satellitePass.payload.max_elevation_deg}° max elev)</p>
                    </>
                  ) : <p>Satellite pass data unavailable.</p>}
                </div>
              </Tile>

              {/* Tile 6: Printer Status */}
              <Tile title="3D Printer" asOf={printerStatus?.occurredAt ?? null} staleAfterMs={5 * 60 * 1000}>
                <div className="printer-tile-content">
                  {printerStatus ? (
                    <>
                      <p>Job: <strong>{printerStatus.payload.job_name ?? "None"}</strong> ({printerStatus.payload.state})</p>
                      <div className="progress-bar-container">
                        <div className="progress-bar-fill" style={{ width: `${clampPercentage(printerStatus.payload.progress_pct)}%` }} />
                      </div>
                      <p className="printer-subtext">
                        {printerStatus.payload.progress_pct === null ? "Progress unavailable" : `${printerStatus.payload.progress_pct}% completed`}
                      </p>
                    </>
                  ) : <p>Printer status unavailable.</p>}
                </div>
              </Tile>

              {/* Tile 7: Roommate Scoreboard */}
              <Tile title="Scoreboard" asOf={scoreboardRows[0]?.occurredAt ?? null}>
                <div className="scoreboard-tile-content">
                  {scoreboardRows.length > 0 ? scoreboardRows.slice(0, 8).map((entry, idx) => (
                    <div key={idx} className="score-row">
                      <span>Resident {idx + 1}</span>
                      <span className="score-points"><strong>{entry.score}</strong> pts</span>
                    </div>
                  )) : <p>Scoreboard unavailable.</p>}
                </div>
              </Tile>

              {/* Tile 8: Daily Food */}
              <Tile title="Daily Food Recommendation" asOf={foodRecommendation?.occurredAt ?? null} staleAfterMs={36 * 60 * 60 * 1000}>
                <div className="food-tile-content">
                  {foodRecommendation ? (
                    <>
                      <h4>{foodRecommendation.payload.title}</h4>
                      <p>{foodRecommendation.payload.detail}</p>
                    </>
                  ) : <p>Food recommendation unavailable.</p>}
                </div>
              </Tile>

              {/* Tile 8b: About this project — static, build-time project facts */}
              <Tile title="About Doorboard">
                <div className="about-tile-content">
                  <p className="about-tagline">{aboutFixture.tagline}</p>
                  <div className="about-stat-chips">
                    <span className="about-chip">
                      <strong>{aboutFixture.stats.lines_of_code.toLocaleString()}</strong> lines
                    </span>
                    <span className="about-chip">
                      <strong>{aboutFixture.stats.languages.length}</strong> languages
                    </span>
                    <span className="about-chip">
                      <strong>{aboutFixture.stats.counts.services}</strong> services
                    </span>
                    <span className="about-chip">
                      <strong>{aboutFixture.stats.counts.contract_event_types}</strong> event types
                    </span>
                    <span className="about-chip">
                      <strong>{aboutFixture.stats.counts.adrs}</strong> ADRs
                    </span>
                  </div>
                  <p className="about-langs">
                    {aboutFixture.stats.languages.map((l) => l.name).join(" · ")}
                  </p>
                  <p className="about-asof">Stats as of {aboutFixture.stats.generated_at}</p>
                </div>
              </Tile>

              {/* Tile 9: Room Poll — fed by the real current poll (T-403) */}
              <Tile title="Active Room Poll">
                <div className="poll-tile-content">
                  {pollAmbientState === "unavailable" && <p>Poll service unavailable; showing no new results.</p>}
                  {pollAmbientState === "ready" && !currentPoll && <p>No poll running right now.</p>}
                  {pollAmbientState === "idle" && <p>Loading current poll…</p>}
                  {currentPoll && (
                    <>
                      <p className="poll-q"><strong>{currentPoll.question}</strong></p>
                      {currentPoll.options.map((opt) => (
                        <PollOptionRow
                          key={opt.id}
                          text={opt.text}
                          votes={pollResults?.find((r) => r.option_id === opt.id)?.votes ?? 0}
                        />
                      ))}
                    </>
                  )}
                </div>
              </Tile>

              {/* Tile 10: Guestbook Highlights — fed by real approved entries (T-403) */}
              <Tile
                title="Guestbook Highlights"
                asOf={approvedGuestbook[0]?.created_at ?? null}
              >
                <div className="guestbook-tile-content">
                  {guestbookAmbientState === "unavailable" && <p>Guestbook unavailable; approved notes below may be stale.</p>}
                  {guestbookAmbientState === "idle" && <p>Loading approved notes…</p>}
                  {guestbookAmbientState === "ready" && approvedGuestbook.length === 0 && <p>No guestbook notes yet — be the first!</p>}
                  {approvedGuestbook.map((e) => (
                    <GuestbookQuote key={e.id} text={e.text} authorLabel={e.author_label} />
                  ))}
                </div>
              </Tile>

              {FEATURE_PHOTOBOOTH && wallboardMoments.length > 0 && (
                <Tile title="Moments" asOf={wallboardMoments[0]?.approved_at ?? null}>
                  <div className="moments-tile-content">
                    {wallboardMoments.slice(0, 3).map((photo) => (
                      <div className="moment-row" key={photo.recording_id}>
                        <div className="moment-thumb-placeholder">
                          {photo.recording_id.slice(0, 2).toUpperCase()}
                        </div>
                        <div>
                          <strong>{photo.tags.length > 0 ? photo.tags.join(", ") : "Photo Booth"}</strong>
                          <p>{photo.recording_id.slice(0, 8)}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </Tile>
              )}

              {/* The "Who's Stopped By" visitor collage is intentionally absent
                  from the public wallboard. It is private all year and only
                  shown on-demand via the owner-only /reveal#<token> page. */}
            </main>
          </div>
        )}
      </CrossfadeSwitch>
    );
  };

  // --- DOORPAD SURFACE ---
  const postDoorApi = useCallback(async (path: string, unavailableMessage = "Local service unavailable") => {
    try {
      const response = await fetch(`${API_BASE}${path}`, { method: "POST" });
      if (!response.ok) {
        triggerToast(unavailableMessage);
        return null;
      }
      return response.json() as Promise<DoorApiSnapshot>;
    } catch {
      triggerToast(unavailableMessage);
      return null;
    }
  }, []);

  const fetchLatestRecording = useCallback(async (): Promise<VideoRecording | null> => {
    for (let attempt = 0; attempt < 12; attempt += 1) {
      try {
        const response = await fetch(`${API_BASE}/doorpad/video-message/latest`);
        if (response.ok) {
          const data = (await response.json()) as { recording?: VideoRecording | null };
          if (data.recording) {
            setLatestRecording(data.recording);
            return data.recording;
          }
        }
      } catch {
        // Retry briefly; final unavailable state is shown by the review screen.
      }
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
    return null;
  }, []);

  const stopRecording = useCallback(async () => {
    if (mediaActionPending) return;
    setMediaActionPending(true);
    const result = await postDoorApi(
      "/doorpad/video-message/stop",
      "Couldn't stop the recording. Please try again."
    );
    const stopped = result?.accepted || result?.session?.state === "VIDEO_MESSAGE_REVIEW";
    if (stopped) {
      setVideoStep("review");
      const recording = await fetchLatestRecording();
      if (!recording) triggerToast("Recording stopped, but playback is still preparing.");
    }
    setMediaActionPending(false);
  }, [fetchLatestRecording, mediaActionPending, postDoorApi]);

  const beginRecording = useCallback(async () => {
    if (mediaActionPending) return;
    setMediaActionPending(true);
    setRecordingElapsed(0);
    setLatestRecording(null);
    const result = await postDoorApi(
      "/doorpad/video-message/start",
      "Recording couldn't start. Check the camera and try again."
    );
    const started = result?.accepted || result?.session?.state === "VIDEO_MESSAGE_RECORDING";
    if (started) setVideoStep("recording");
    else setVideoStep("offer");
    setMediaActionPending(false);
  }, [mediaActionPending, postDoorApi]);

  useEffect(() => {
    if (doorPadScreen !== "message" || videoStep !== "countdown") return undefined;
    if (countdown <= 0) {
      if (mediaActionPending) return undefined;
      void beginRecording();
      return undefined;
    }
    const timer = window.setTimeout(() => setCountdown((value) => value - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [beginRecording, countdown, doorPadScreen, mediaActionPending, videoStep]);

  useEffect(() => {
    if (doorPadScreen !== "message" || videoStep !== "recording") return undefined;
    const timer = window.setInterval(() => {
      setRecordingElapsed((value) => {
        const next = value + 1;
        if (next >= maxRecordingS) {
          window.clearInterval(timer);
          void stopRecording();
        }
        return next;
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [doorPadScreen, maxRecordingS, stopRecording, videoStep]);

  const startVideoFlow = async () => {
    if (!storageStatus.recording_allowed) {
      triggerToast("Video messages are temporarily unavailable because local storage is paused.");
      return;
    }
    if (mediaActionPending) return;
    setDoorPadScreen("message");
    setVideoStep("countdown");
    setCountdown(3);
    setLatestRecording(null);
    setMediaActionPending(true);
    const result = await postDoorApi(
      "/doorpad/video-message/offer",
      "Video messages are temporarily unavailable."
    );
    const offered = result?.accepted || result?.session?.state === "VIDEO_MESSAGE_OFFERED";
    if (!offered) setVideoStep("offer");
    if (result?.config?.max_recording_s) setMaxRecordingS(result.config.max_recording_s);
    setMediaActionPending(false);
  };

  const discardVideoFlow = async () => {
    if (mediaActionPending) return;
    setMediaActionPending(true);
    const result = await postDoorApi(
      "/doorpad/video-message/discard",
      "Couldn't discard the message yet. Please try again."
    );
    const discarded = result?.accepted || ["SESSION_END", "IDLE"].includes(result?.session?.state ?? "");
    if (discarded) {
      setLatestRecording(null);
      setVisitorQrUrl(null);
      if (result?.session) applySessionSnapshot(result.session);
      else setDoorPadScreen("home");
      setVideoStep("offer");
    }
    setMediaActionPending(false);
  };

  const saveVideoMessage = async () => {
    if (mediaActionPending) return;
    setMediaActionPending(true);
    const result = await postDoorApi(
      "/doorpad/video-message/save",
      "Couldn't save the message. Your review is still here."
    );
    const saved = result?.accepted || result?.session?.state === "VIDEO_MESSAGE_SAVED";
    if (saved) setVideoStep("saved");
    setMediaActionPending(false);
  };

  const startPhotoFlow = async () => {
    if (!FEATURE_PHOTOBOOTH) return;
    if (!storageStatus.recording_allowed) {
      triggerToast("Photo capture is temporarily unavailable because local storage is paused.");
      return;
    }
    setDoorPadScreen("photo");
    setPhotoStep("offer");
    setCurrentPhoto(null);
  };

  const beginPhotoCountdown = () => {
    setPhotoStep("countdown");
    setPhotoCountdown(3);
    setCurrentPhoto(null);
  };

  const capturePhoto = useCallback(async () => {
    if (!FEATURE_PHOTOBOOTH) return;
    setMediaActionPending(true);
    try {
      const response = await fetch(`${API_BASE}/doorpad/photo-booth/capture`, { method: "POST" });
      if (!response.ok) {
        triggerToast("Photo capture unavailable");
        setPhotoStep("offer");
        return;
      }
      const data = (await response.json()) as { photo: PhotoReview };
      setCurrentPhoto(data.photo);
      setPhotoStep("review");
    } catch {
      triggerToast("Photo capture unavailable");
      setPhotoStep("offer");
    } finally {
      setMediaActionPending(false);
    }
  }, []);

  useEffect(() => {
    if (doorPadScreen !== "photo" || photoStep !== "countdown") return undefined;
    if (photoCountdown <= 0) {
      void capturePhoto();
      return undefined;
    }
    const timer = window.setTimeout(() => setPhotoCountdown((value) => value - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [capturePhoto, doorPadScreen, photoCountdown, photoStep]);

  const discardPhotoFlow = async (): Promise<boolean> => {
    const photo = currentPhoto;
    if (!photo) {
      setCurrentPhoto(null);
      setDoorPadScreen("home");
      setPhotoStep("offer");
      return true;
    }
    if (mediaActionPending) return false;
    setMediaActionPending(true);
    try {
      const response = await fetch(`${API_BASE}/doorpad/photo-booth/${photo.recording_id}/discard`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: photo.session_id }),
      });
      if (!response.ok) {
        triggerToast("Couldn't discard the photo yet. Please try again.");
        return false;
      }
      setCurrentPhoto(null);
      setDoorPadScreen("home");
      setPhotoStep("offer");
      return true;
    } catch {
      triggerToast("Couldn't discard the photo yet. Please try again.");
      return false;
    } finally {
      setMediaActionPending(false);
    }
  };

  const retakePhoto = async () => {
    if (await discardPhotoFlow()) {
      setDoorPadScreen("photo");
      beginPhotoCountdown();
    }
  };

  const savePhoto = async () => {
    if (!currentPhoto || mediaActionPending) return;
    setMediaActionPending(true);
    try {
      const response = await fetch(
        `${API_BASE}/doorpad/photo-booth/${currentPhoto.recording_id}/save`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: currentPhoto.session_id }),
        }
      );
      if (!response.ok) {
        triggerToast("Couldn't save photo");
        return;
      }
      setPhotoStep("saved");
      triggerToast("Photo saved for owner review.");
    } catch {
      triggerToast("Couldn't save photo");
    } finally {
      setMediaActionPending(false);
    }
  };

  // --- Post-bell photo check-in --------------------------------------------
  // Auto-capture a single photo through the existing photo-booth pipeline once
  // the ringing screen is up. The capture is guarded by `postRingPhotoStatus`
  // (only fires from "idle") so it never loops.
  const capturePostRingPhoto = useCallback(async () => {
    if (!FEATURE_PHOTOBOOTH) return;
    setPostRingPhotoStatus("capturing");
    // Pre-fill the optional name with the recognized enrolled name (if any)
    // without clobbering anything the visitor already typed.
    setPostRingName((previous) => previous || (activeDisplayName ?? ""));
    try {
      const response = await fetch(`${API_BASE}/doorpad/photo-booth/capture`, { method: "POST" });
      if (!response.ok) {
        setPostRingPhotoStatus("unavailable");
        return;
      }
      const data = (await response.json()) as { photo: PhotoReview };
      setPostRingPhoto(data.photo);
      setPostRingPhotoStatus("ready");
    } catch {
      setPostRingPhotoStatus("unavailable");
    }
  }, [activeDisplayName]);

  // Best-effort discard of the still-private capture — used both for "No thanks"
  // and discard-on-abandon. Status goes terminal ("cleared") first so the
  // auto-capture effect can't re-fire and the abandon effect can't double-run.
  const discardPostRingPhoto = useCallback(async (): Promise<void> => {
    const photo = postRingPhoto;
    setPostRingPhoto(null);
    setPostRingPhotoStatus("cleared");
    if (!photo) return;
    try {
      await fetch(`${API_BASE}/doorpad/photo-booth/${photo.recording_id}/discard`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: photo.session_id }),
      });
    } catch {
      // The capture stays private in the photo-booth pipeline and remains
      // owner-deletable, so a failed discard never exposes anything publicly.
    }
  }, [postRingPhoto]);

  const declinePostRingCheckin = useCallback(() => {
    void discardPostRingPhoto();
  }, [discardPostRingPhoto]);

  const submitPostRingCheckin = async () => {
    if (postRingCheckinPending || postRingPhotoStatus === "saving") return;
    setPostRingCheckinPending(true);
    setPostRingPhotoStatus("saving");
    const photo = postRingPhoto;
    try {
      let photoRecordingId: string | null = null;
      if (photo) {
        // Save through the photo-booth pipeline first so the recording is
        // persisted (privately, for owner review) before the check-in links it.
        const response = await fetch(
          `${API_BASE}/doorpad/photo-booth/${photo.recording_id}/save`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: photo.session_id }),
          }
        );
        if (!response.ok) {
          triggerToast("Couldn't save your photo — try again.");
          setPostRingPhotoStatus("ready");
          return;
        }
        photoRecordingId = photo.recording_id;
      }
      const label = postRingName.trim() || activeDisplayName || "Guest";
      // door-api derives attribution server-side; this client only passes the
      // optional photo reference and a display label.
      const checkin = await socialApi.createCheckin(label, photoRecordingId);
      rememberMyContent({ kind: "checkin", id: checkin.id, label });
      setPostRingPhoto(null);
      setPostRingPhotoStatus("saved");
      triggerToast("Check-in saved.");
    } catch (err) {
      triggerToast(apiErrorMessage(err, "Couldn't check in — try again."));
      setPostRingPhotoStatus(photo ? "ready" : "unavailable");
    } finally {
      setPostRingCheckinPending(false);
    }
  };

  // Fire the single auto-capture shortly after the ringing screen appears.
  useEffect(() => {
    if (!FEATURE_PHOTOBOOTH) return undefined;
    if (doorPadScreen !== "ringing") return undefined;
    if (sessionState === "ANSWERED") return undefined;
    if (!storageStatus.recording_allowed) return undefined;
    if (postRingPhotoStatus !== "idle") return undefined;
    const timer = window.setTimeout(() => {
      void capturePostRingPhoto();
    }, POST_RING_PHOTO_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [
    doorPadScreen,
    sessionState,
    storageStatus.recording_allowed,
    postRingPhotoStatus,
    capturePostRingPhoto,
  ]);

  // Discard an unsaved capture the moment the visit is answered or the ringing
  // screen is left, so an abandoned photo is never left lingering.
  useEffect(() => {
    if (!FEATURE_PHOTOBOOTH) return;
    const abandoned = doorPadScreen !== "ringing" || sessionState === "ANSWERED";
    if (abandoned && postRingPhotoStatus === "ready") {
      void discardPostRingPhoto();
    }
  }, [doorPadScreen, sessionState, postRingPhotoStatus, discardPostRingPhoto]);

  const showVisitorQr = async () => {
    setDoorPadScreen("message");
    setVideoStep("qr");
    try {
      const response = await fetch(`${API_BASE}/visitor-token`);
      if (response.ok) {
        const data = (await response.json()) as { token: string; url: string };
        socialApi.setVisitorToken(data.token);
        setVisitorQrUrl(data.url);
      } else {
        triggerToast("QR token unavailable");
      }
    } catch {
      triggerToast("QR token unavailable");
    }
  };

  const ringDoorbell = async () => {
    if (
      ringRequestState === "sending" ||
      (sessionState === "RINGING" && ringRequestState !== "failed")
    ) {
      triggerToast("The bell is already ringing.");
      return;
    }
    // Discard any capture left over from a prior ring, then reset the check-in
    // photo state so the ringing screen auto-captures a fresh one.
    if (postRingPhoto && postRingPhotoStatus === "ready") {
      void discardPostRingPhoto();
    }
    setPostRingPhoto(null);
    setPostRingPhotoStatus("idle");
    setPostRingName("");
    setPostRingCheckinPending(false);
    setSessionState("RINGING");
    setSessionObservedAt(Date.now());
    setDoorPadScreen("ringing");
    setRingRequestState("sending");
    const result = await postDoorApi(
      "/doorpad/ring",
      "The local bell service is unavailable. You can still leave a video message."
    );
    if (!result) {
      setRingRequestState("failed");
      return;
    }
    applySessionSnapshot(result.session);
    setRingRequestState("sent");
    if (!result.accepted) triggerToast("The bell is already active.");
  };

  const renderVideoPreview = () => <LiveVideoPreview title="Live self-preview" />;

  const renderDoorPad = () => {
    const handleActionClick = (actionName: string, targetScreen: DoorPadScreen) => {
      if (targetScreen === "ringing") {
        ringDoorbell();
      } else {
        setDoorPadScreen(targetScreen);
        triggerToast(`${actionName} flow opened`);
      }
    };

    if (doorPadScreen === "home") {
      return (
        <div
          className="doorpad-view db-app-theme"
          ref={doorPadFocusRef}
          tabIndex={-1}
          aria-label="DoorPad home"
        >
          <header className="doorpad-header">
            <h2>Room {ROOM_LABEL} DoorPad</h2>
            {RESIDENTS_LABEL && <p className="doorpad-residents">{RESIDENTS_LABEL}</p>}
            <p>Large-touch visitor controls</p>
          </header>
          
          <div className="doorpad-grid">
            <BigButton id="btn-ring" variant="primary" icon={<span aria-hidden="true">R</span>} onClick={ringDoorbell}>
              Ring Bell
            </BigButton>
            
            <BigButton id="btn-video" icon={<span aria-hidden="true">V</span>} onClick={() => {
              setDoorPadScreen("message");
              setVideoStep("offer");
            }}>
              Video Message
            </BigButton>

            {FEATURE_PHOTOBOOTH && (
              <BigButton id="btn-photo-booth" icon={<span aria-hidden="true">P</span>} onClick={startPhotoFlow}>
                Photo Booth
              </BigButton>
            )}

            <BigButton id="btn-guestbook" icon={<span aria-hidden="true">G</span>} onClick={() => handleActionClick("Guestbook", "guestbook")}>
              Guestbook
            </BigButton>

            <BigButton id="btn-poll" icon={<span aria-hidden="true">Q</span>} onClick={() => handleActionClick("Poll Vote", "poll")}>
              Vote in Poll
            </BigButton>

            <BigButton id="btn-checkin" icon={<span aria-hidden="true">C</span>} onClick={() => handleActionClick("Check In", "checkin")}>
              Visitor Check-In
            </BigButton>

            <BigButton id="btn-remote" icon={<span aria-hidden="true">W</span>} onClick={() => handleActionClick("Wallboard", "remote")}>
              Wallboard Control
            </BigButton>

            <BigButton id="btn-privacy" icon={<span aria-hidden="true">I</span>} onClick={() => handleActionClick("Privacy Notice", "privacy")}>
              Privacy & Info
            </BigButton>
          </div>
        </div>
      );
    }

    return (
      <CountdownAutoReset
        onReset={
          doorPadScreen === "message"
            ? videoStep === "qr"
              ? handleReset
              : discardVideoFlow
            : doorPadScreen === "photo"
              ? discardPhotoFlow
              : handleReset
        }
        timeoutMs={30000}
        paused={
          (doorPadScreen === "message" && videoStep !== "offer" && videoStep !== "qr") ||
          doorPadScreen === "photo"
        }
      >
        <div
          className="doorpad-view db-app-theme fade-in"
          ref={doorPadFocusRef}
          tabIndex={-1}
          aria-label="DoorPad visitor workflow"
        >
          {doorPadScreen === "ringing" && (
            <div className="doorpad-sub-content doorpad-waiting-content">
              <div className="doorpad-waiting-layout">
                <LiveVideoPreview
                  title="Live view at the door"
                  className="doorpad-waiting-video"
                />
                <div className="doorpad-waiting-panel">
                  <div className="doorpad-journey-status" role="status" aria-live="polite">
                    <span className="doorpad-journey-status__mark" aria-hidden="true" />
                    <div>
                      <p className="surface-eyebrow">Visitor session</p>
                      <h2>
                        {sessionState === "ANSWERED"
                          ? "Someone is coming"
                          : sessionState === "UNANSWERED_TIMEOUT" || sessionState === "VIDEO_MESSAGE_OFFERED"
                            ? "No answer yet"
                            : ringRequestState === "failed"
                              ? "Bell service unavailable"
                              : "Bell sent"}
                      </h2>
                      <p>
                        {sessionState === "ANSWERED"
                          ? "Please wait here while they come to the door."
                          : sessionState === "UNANSWERED_TIMEOUT" || sessionState === "VIDEO_MESSAGE_OFFERED"
                            ? "You can leave a video message now or keep waiting a little longer."
                            : ringRequestState === "failed"
                              ? "The local bell service did not confirm the ring. Retry here or leave a video message."
                              : "The bell is ringing inside. You can wait here or leave a video message now."}
                      </p>
                    </div>
                  </div>
                  {FEATURE_PHOTOBOOTH &&
                    sessionState !== "ANSWERED" &&
                    (postRingPhotoStatus === "capturing" ||
                      postRingPhotoStatus === "ready" ||
                      postRingPhotoStatus === "unavailable" ||
                      postRingPhotoStatus === "saving") && (
                      <div
                        id="post-ring-checkin"
                        className="doorpad-photo-checkin"
                        role="group"
                        aria-label="Photo check-in"
                      >
                        <div className="doorpad-photo-checkin__header">
                          <h3>Want to check in with your picture?</h3>
                          <p className="placeholder-subtext">
                            It's for a fun end-of-year collage of everyone who stopped by — with fun
                            stats about them.
                          </p>
                        </div>
                        {postRingPhotoStatus === "ready" && postRingPhoto ? (
                          <img
                            className="review-photo"
                            src={postRingPhoto.review_url}
                            alt="Your check-in photo"
                          />
                        ) : postRingPhotoStatus === "unavailable" ? (
                          <p className="placeholder-subtext">
                            Camera unavailable right now — you can still check in without a photo.
                          </p>
                        ) : (
                          <div className="video-preview-frame video-preview-frame--unavailable">
                            Taking your photo…
                          </div>
                        )}
                        <label className="doorpad-photo-checkin__name">
                          <span>Name (optional)</span>
                          <input
                            className="doorpad-photo-checkin__input"
                            type="text"
                            value={postRingName}
                            maxLength={60}
                            placeholder="Guest"
                            onChange={(event) => setPostRingName(event.target.value)}
                          />
                        </label>
                        <div className="action-button-group">
                          <BigButton
                            id="post-ring-checkin-yes"
                            variant="primary"
                            disabled={
                              postRingCheckinPending ||
                              postRingPhotoStatus === "capturing" ||
                              postRingPhotoStatus === "saving"
                            }
                            onClick={submitPostRingCheckin}
                          >
                            Yes, check in
                          </BigButton>
                          <BigButton
                            id="post-ring-checkin-no"
                            disabled={postRingCheckinPending}
                            onClick={declinePostRingCheckin}
                          >
                            No thanks
                          </BigButton>
                        </div>
                      </div>
                    )}
                  {FEATURE_PHOTOBOOTH && postRingPhotoStatus === "saved" && (
                    <div className="doorpad-photo-checkin doorpad-photo-checkin--done" role="status">
                      <h3>Checked in — thanks!</h3>
                      <p className="placeholder-subtext">
                        Your photo is saved privately for owner review.
                      </p>
                    </div>
                  )}
                  <div className="doorpad-waiting-actions">
                    <BigButton
                      id="post-ring-wait"
                      variant="primary"
                      onClick={
                        ringRequestState === "failed"
                          ? ringDoorbell
                          : () => triggerToast("Still waiting — this screen will stay with you.")
                      }
                    >
                      {ringRequestState === "failed" ? "Retry Bell" : "Wait for Someone to Open"}
                    </BigButton>
                    {sessionState !== "ANSWERED" && (
                      <BigButton
                        id="post-ring-video"
                        disabled={mediaActionPending || !storageStatus.recording_allowed}
                        onClick={startVideoFlow}
                      >
                        Send a Video Message
                      </BigButton>
                    )}
                    {!FEATURE_PHOTOBOOTH && (
                      <BigButton id="post-ring-checkin" onClick={() => setDoorPadScreen("checkin")}>
                        Check In
                      </BigButton>
                    )}
                    <BigButton onClick={showVisitorQr}>Open Visitor QR</BigButton>
                  </div>
                </div>
              </div>
              <div className="action-button-group doorpad-waiting-footer">
                <BigButton onClick={() => setDoorPadScreen("guestbook")}>Guestbook</BigButton>
                <BigButton onClick={() => setDoorPadScreen("poll")}>Vote in Poll</BigButton>
                <BigButton onClick={handleReset}>End Visit</BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "message" && videoStep === "offer" && (
            <div className="doorpad-sub-content">
              <h2>Leave a Video Message</h2>
              <p>A short visitor-initiated message can be reviewed before saving.</p>
              {renderVideoPreview()}
              <div className="action-button-group">
                <BigButton variant="primary" onClick={startVideoFlow}>Start Recording</BigButton>
                <BigButton onClick={showVisitorQr}>Open Visitor QR</BigButton>
                <BigButton onClick={discardVideoFlow}>Cancel</BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "message" && videoStep === "countdown" && (
            <div className="doorpad-sub-content">
              <h2>Recording Starts In</h2>
              <div className="countdown-number">{countdown}</div>
              {renderVideoPreview()}
              <div className="action-button-group">
                <BigButton onClick={discardVideoFlow}>Abort</BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "message" && videoStep === "recording" && (
            <div className="doorpad-sub-content">
              <div className="recording-status"><span /> Recording {recordingElapsed}s / {maxRecordingS}s</div>
              {renderVideoPreview()}
              <div className="action-button-group">
                <BigButton variant="primary" disabled={mediaActionPending} onClick={stopRecording}>Stop & Review</BigButton>
                <BigButton disabled={mediaActionPending} onClick={discardVideoFlow}>Discard</BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "message" && videoStep === "review" && (
            <div className="doorpad-sub-content">
              <h2>Review Message</h2>
              {latestRecording?.playback_url ? (
                <video className="review-video" src={latestRecording.playback_url} controls playsInline />
              ) : (
                <div className="video-preview-frame video-preview-frame--unavailable">Preparing playback...</div>
              )}
              <div className="message-meta">
                Consent context: {latestRecording?.consent_context ?? "visitor_initiated"}
              </div>
              <div className="action-button-group">
                <BigButton variant="primary" disabled={mediaActionPending} onClick={saveVideoMessage}>Save Message</BigButton>
                <BigButton disabled={mediaActionPending} onClick={startVideoFlow}>Re-record</BigButton>
                <BigButton disabled={mediaActionPending} onClick={discardVideoFlow}>Discard</BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "message" && videoStep === "saved" && (
            <div className="doorpad-sub-content">
              <h2>Message Saved</h2>
              <p>Thanks. The saved message is now in the local admin inbox.</p>
              <div className="action-button-group">
                <BigButton variant="primary" onClick={handleReset}>Done</BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "message" && videoStep === "qr" && (
            <div className="doorpad-sub-content">
              <h2>Visitor Link</h2>
              <p>Use your phone to see ring status, leave a text note, vote, or request deletion.</p>
              {visitorQrUrl ? <QRPlaceholder url={visitorQrUrl} /> : <p>Preparing QR token...</p>}
              <div className="action-button-group">
                <BigButton variant="primary" onClick={startVideoFlow}>Record a Video Here</BigButton>
                <BigButton onClick={returnDoorPadToContext}>Back</BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "photo" && photoStep === "offer" && (
            <div className="doorpad-sub-content">
              <h2>Take a Photo</h2>
              <p>
                The camera will count down before taking one photo. You can review, retake, or
                discard it before anything is saved privately for owner approval.
              </p>
              {renderVideoPreview()}
              <div className="action-button-group">
                <BigButton variant="primary" onClick={beginPhotoCountdown}>Start 3-Second Countdown</BigButton>
                <BigButton onClick={discardPhotoFlow}>Cancel</BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "photo" && photoStep === "countdown" && (
            <div className="doorpad-sub-content">
              <h2>Photo In</h2>
              <div className="countdown-number">{photoCountdown}</div>
              {renderVideoPreview()}
              <div className="action-button-group">
                <BigButton onClick={discardPhotoFlow}>Cancel</BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "photo" && photoStep === "review" && (
            <div className="doorpad-sub-content">
              <h2>Review Photo</h2>
              {currentPhoto ? (
                <img className="review-photo" src={currentPhoto.review_url} alt="Photo booth review" />
              ) : (
                <div className="video-preview-frame video-preview-frame--unavailable">
                  Preparing photo...
                </div>
              )}
              <div className="message-meta">Consent context: visitor_initiated</div>
              <div className="action-button-group">
                <BigButton variant="primary" disabled={mediaActionPending} onClick={savePhoto}>Keep Photo</BigButton>
                <BigButton disabled={mediaActionPending} onClick={retakePhoto}>Retake</BigButton>
                <BigButton disabled={mediaActionPending} onClick={discardPhotoFlow}>Discard</BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "photo" && photoStep === "saved" && (
            <div className="doorpad-sub-content">
              <h2>Photo Saved</h2>
              <p>Saved privately for owner review before it appears anywhere else.</p>
              <div className="action-button-group">
                <BigButton variant="primary" onClick={() => setDoorPadScreen("home")}>
                  Done
                </BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "remote" && (
            <div className="doorpad-sub-content doorpad-remote-content">
              <div className="doorpad-section-heading">
                <div>
                  <p className="surface-eyebrow">Wallboard remote</p>
                  <h2>Choose what the big display shows</h2>
                </div>
                <span className="doorpad-launch-status">{wallboardLaunchStatus}</span>
              </div>
              <WallboardLauncher
                selectedChannel={wallboardFocusRequest?.mode === "focus" && wallboardFocusRequest.channel
                  ? wallboardFocusRequest.channel
                  : "ambient"}
                onSelect={setWallboardChannel}
              />
              <p className="placeholder-subtext">
                Local mock control only until the display-control contract escalation is approved.
              </p>
              <div className="action-button-group">
                <BigButton variant="primary" onClick={returnDoorPadToContext}>Done</BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "guestbook" && (
            <div className={`doorpad-sub-content${guestbookKeyboardOpen ? " osk-open" : ""}`}>
              <h2>Leave a Guestbook Note</h2>
              <p className="placeholder-subtext">Pick a phrase or write a short note (280 chars max)</p>
              <div className="phrase-grid">
                {CANNED_GUESTBOOK_PHRASES.map((phrase) => (
                  <button
                    key={phrase}
                    className="phrase-btn"
                    disabled={guestbookSubmitting}
                    aria-pressed={selectedGuestbookPhrase === phrase}
                    onClick={() => {
                      setSelectedGuestbookPhrase(phrase);
                      setGuestbookText("");
                    }}
                  >
                    "{phrase}"
                  </button>
                ))}
              </div>
              {selectedGuestbookPhrase && (
                <BigButton
                  variant="primary"
                  disabled={guestbookSubmitting}
                  onClick={() => handleGuestbookSubmit(selectedGuestbookPhrase)}
                >
                  Send Selected Phrase
                </BigButton>
              )}
              <textarea
                className="guestbook-freetext"
                maxLength={280}
                rows={3}
                placeholder="Tap here to type your own note..."
                value={guestbookText}
                onFocus={(e) => {
                  setGuestbookKeyboardOpen(true);
                  // Scroll the field to the top of the view so it sits ABOVE the
                  // fixed on-screen keyboard (which covers the bottom ~half of the
                  // 1024x600 kiosk). `center` used to land it behind the keyboard.
                  window.setTimeout(() => e.target.scrollIntoView({ block: "start" }), 0);
                }}
                onChange={(e) => {
                  setGuestbookText(e.target.value);
                  setSelectedGuestbookPhrase(null);
                }}
              />
              <p className="character-count" aria-live="polite">{guestbookText.length} / 280</p>
              <div className="action-button-group">
                <BigButton
                  variant="primary"
                  disabled={guestbookSubmitting || guestbookText.trim().length === 0}
                  onClick={() => {
                    setGuestbookKeyboardOpen(false);
                    handleGuestbookSubmit(guestbookText);
                  }}
                >
                  Submit Note
                </BigButton>
                <BigButton
                  onClick={() => {
                    setGuestbookKeyboardOpen(false);
                    returnDoorPadToContext();
                  }}
                >
                  Cancel
                </BigButton>
              </div>
              {guestbookKeyboardOpen && (
                <OnScreenKeyboard
                  value={guestbookText}
                  maxLength={280}
                  onChange={(v) => {
                    setGuestbookText(v);
                    setSelectedGuestbookPhrase(null);
                  }}
                  onClose={() => setGuestbookKeyboardOpen(false)}
                />
              )}
            </div>
          )}

          {doorPadScreen === "poll" && (
            <div className="doorpad-sub-content">
              <h2>Vote in Poll</h2>
              {!currentPoll && <p>No poll is running right now — check back later!</p>}
              {currentPoll && (
                <>
                  <p className="poll-q"><strong>{currentPoll.question}</strong></p>
                  {pollVoteError && <p className="poll-error">{pollVoteError}</p>}
                  <div className="poll-choices">
                    {currentPoll.options.map((opt) => {
                      const result = pollResults?.find((r) => r.option_id === opt.id);
                      return (
                        <button
                          key={opt.id}
                          className="phrase-btn"
                          style={{ width: "100%", margin: "4px 0" }}
                          disabled={doorPadVotedOptionId !== null}
                          aria-pressed={
                            doorPadVotedOptionId === opt.id || selectedPollOptionId === opt.id
                          }
                          onClick={() => setSelectedPollOptionId(opt.id)}
                        >
                          {opt.text}
                          {doorPadVotedOptionId && result !== undefined && (
                            <span className="poll-vote-count"> — {result.votes} votes</span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                </>
              )}
              {doorPadVotedOptionId && <p className="visitor-note-status">Vote submitted.</p>}
              <div className="action-button-group">
                {!doorPadVotedOptionId && currentPoll && (
                  <BigButton
                    variant="primary"
                    disabled={!selectedPollOptionId}
                    onClick={handlePollVote}
                  >
                    Submit Vote
                  </BigButton>
                )}
                {doorPadVotedOptionId && (
                  <BigButton variant="primary" onClick={returnDoorPadToContext}>Done</BigButton>
                )}
                <BigButton onClick={returnDoorPadToContext}>Cancel</BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "checkin" && (
            <div className="doorpad-sub-content">
              <h2>Check In</h2>
              <p>Voluntarily mark yourself as a visitor to increment stats!</p>
              <div className="phrase-grid">
                {activeDisplayName && (
                  <button
                    className="phrase-btn"
                    disabled={checkinSubmitting}
                    onClick={() => handleCheckin("enrolled")}
                  >
                    Check in as {activeDisplayName}
                  </button>
                )}
                <button
                  className="phrase-btn"
                  disabled={checkinSubmitting}
                  onClick={() => handleCheckin("guest")}
                >
                  Check in as Guest
                </button>
              </div>
              {!activeDisplayName && (
                <p className="placeholder-subtext">
                  Named check-in is available only when an enrolled, consenting visitor is recognized.
                </p>
              )}
              <div className="action-button-group">
                <BigButton onClick={returnDoorPadToContext}>Back</BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "privacy" && (
            <div className="doorpad-sub-content">
              <h2>Camera Notice & Deletion Requests</h2>
              <div className="privacy-info-box">
                <p>This door pad uses local cameras for enrolled-resident recognition only.</p>
                <p>Unknown visitors are never named, and biometric data stays offline on the device.</p>
              </div>
              {myContent.length > 0 && (
                <div className="my-content-list">
                  <p>Things you've submitted this session:</p>
                  {myContent.map((item) => (
                    <div key={item.id} className="my-content-row">
                      <span>{item.kind}: {item.label}</span>
                      <button className="phrase-btn" onClick={() => handleDeletionRequest(item)}>
                        Delete
                      </button>
                    </div>
                  ))}
                </div>
              )}
              {myContent.length === 0 && (
                <p className="placeholder-subtext">
                  Nothing submitted yet this session — guestbook notes and check-ins will show
                  up here with a delete option.
                </p>
              )}
              <div className="action-button-group">
                <BigButton
                  variant="primary"
                  disabled={myContent.length === 0}
                  onClick={handleDeletionRequestAll}
                >
                  Request Deletion of My Data
                </BigButton>
                <BigButton onClick={returnDoorPadToContext}>Back</BigButton>
              </div>
            </div>
          )}
        </div>
      </CountdownAutoReset>
    );
  };

  // --- VISITOR SURFACE ---
  const renderVisitor = () => {
    return (
      <main className="visitor-shell db-app-theme">
        <VisitorPage sessionState={sessionState} />
      </main>
    );
  };

  // --- LIVE VIEW DEMO ---
  const renderLiveViewDemo = () => {
    return (
      <div className="live-demo-view db-app-theme">
        <main className="live-demo-panel">
          <LiveVideoPreview title="Door Camera Preview" showStats />
          <div className="action-button-group">
            <BigButton onClick={() => navigateTo("/")}>
              Back
            </BigButton>
          </div>
        </main>
      </div>
    );
  };

  const submitAdminToken = (event: React.FormEvent) => {
    event.preventDefault();
    const token = adminTokenDraft.trim();
    if (!token) return;
    window.localStorage.setItem(ADMIN_TOKEN_KEY, token);
    setAdminToken(token);
    setAdminTokenDraft("");
  };

  const logoutAdmin = () => {
    window.localStorage.removeItem(ADMIN_TOKEN_KEY);
    setAdminToken("");
    setAdminRecordings([]);
    setAdminInboxState("idle");
  };

  const runAdminSessionAction = async (path: "answer" | "cannot-answer" | "end") => {
    try {
      const response = await fetch(`${API_BASE}/admin/session/${path}`, {
        method: "POST",
        headers: adminHeaders(),
      });
      if (!response.ok) {
        triggerToast(response.status === 401 ? "Admin token rejected" : "Session action unavailable");
        return;
      }
      const result = (await response.json()) as DoorApiSnapshot;
      if (result.session) applySessionSnapshot(result.session);
      triggerToast(result.accepted ? "Session updated" : "That action no longer applies");
    } catch {
      triggerToast("Session action unavailable");
    }
  };

  // --- ADMIN SURFACE ---
  const renderAdmin = () => {
    if (!adminToken) {
      return (
        <main className="admin-auth-shell db-app-theme">
          <form className="admin-auth-card" onSubmit={submitAdminToken}>
            <p className="surface-eyebrow">Owner access</p>
            <h1>Admin sign in</h1>
            <p>Enter the Pi-local admin token. Admin tools are never linked from visitor screens.</p>
            <label htmlFor="admin-token">Admin token</label>
            <input
              id="admin-token"
              type="password"
              autoComplete="current-password"
              value={adminTokenDraft}
              onChange={(event) => setAdminTokenDraft(event.target.value)}
            />
            <BigButton variant="primary" disabled={!adminTokenDraft.trim()} type="submit">
              Continue
            </BigButton>
          </form>
        </main>
      );
    }

    // SSD Space percentage calculation: 48 GiB default cap
    const maxSsdCap = 48 * 1024 * 1024 * 1024;
    const freeGb = storageStatus.free_bytes / (1024 * 1024 * 1024);
    const freePct = Math.min(100, (storageStatus.free_bytes / maxSsdCap) * 100);
    const ssdSeverity = freeGb < 4 ? "critical" : freeGb < 8 ? "warning" : "normal";

    // Queue depth severity
    const queueSeverity = storageStatus.queue_depth > 15 ? "critical" : storageStatus.queue_depth > 5 ? "warning" : "normal";

    // Oldest unsynced severity
    const oldestHrs = storageStatus.oldest_unsynced_s / 3600;
    const oldestSeverity = oldestHrs > 120 ? "critical" : oldestHrs > 48 ? "warning" : "normal";

    return (
      <div className="admin-view db-app-theme fade-in">
        <div className="admin-container">
          <header className="admin-header">
            <div>
              <h1>Admin Control Panel</h1>
              <p className="subtitle-tag">Owner-only local controls</p>
            </div>
            {DEV_TOOLS_ENABLED && (
              <button className="back-home-btn" onClick={() => navigateTo("/")}>Back to Navigation</button>
            )}
            <button className="back-home-btn" onClick={logoutAdmin}>Sign out</button>
          </header>

          <section className="admin-live-session" aria-live="polite">
            <div className="admin-live-session__summary">
              <p className="surface-eyebrow">Live visitor session</p>
              <h2>{sessionState === "IDLE" ? "No active visitor" : sessionState.replaceAll("_", " ")}</h2>
              <p>
                {sessionState === "IDLE"
                  ? "The DoorPad and wallboard are in ambient mode."
                  : `Observed ${Math.max(0, Math.floor((currentTime.getTime() - sessionObservedAt) / 1000))} seconds ago.`}
              </p>
              <div className="action-button-group">
                <BigButton
                  variant="primary"
                  disabled={sessionState !== "RINGING"}
                  onClick={() => runAdminSessionAction("answer")}
                >
                  Someone Is Coming
                </BigButton>
                <BigButton
                  disabled={sessionState !== "RINGING"}
                  onClick={() => runAdminSessionAction("cannot-answer")}
                >
                  Can't Answer
                </BigButton>
                <BigButton
                  disabled={sessionState === "IDLE"}
                  onClick={() => runAdminSessionAction("end")}
                >
                  End Session
                </BigButton>
              </div>
            </div>
            <LiveVideoPreview title="Local Door Camera" showStats />
          </section>

          <div className="admin-stats-grid">
            <div className="stat-card">
              <span>door-api</span>
              <strong>Pi-local</strong>
              <small>Session and social writes use local endpoints.</small>
            </div>
            <div className="stat-card">
              <span>Media storage</span>
              <strong>
                {!storageStatusKnown
                  ? "Status unavailable"
                  : storageStatus.recording_allowed
                    ? "Recording allowed"
                    : "Recording paused"}
              </strong>
              <small>
                {storageStatusKnown
                  ? `${freeGb.toFixed(1)} GiB free from the latest local storage event.`
                  : "Waiting for a media.storage_status update; no capacity is being guessed."}
              </small>
            </div>
            <div className="stat-card">
              <span>Vision pipeline</span>
              <strong>Unavailable in this panel</strong>
              <small>Diagnostics should report Hailo/camera status when connected.</small>
            </div>
            <div className="stat-card">
              <span>Sync queue</span>
              <strong>{storageStatusKnown ? `${storageStatus.queue_depth} pending` : "Status unavailable"}</strong>
              <small>
                {storageStatusKnown
                  ? `Oldest unsynced: ${oldestHrs.toFixed(1)} hours.`
                  : "Waiting for local media telemetry."}
              </small>
            </div>
          </div>

          <section className="admin-inbox-section">
            <h2>Video Message Inbox</h2>
            {adminInboxState === "loading" && <p className="desc">Loading saved messages…</p>}
            {adminInboxState === "unavailable" && (
              <p className="admin-unavailable">Message inbox unavailable. Check the admin token and door-media health.</p>
            )}
            {adminInboxState === "ready" && adminRecordings.length === 0 ? (
              <p className="desc">No saved visitor video messages.</p>
            ) : adminInboxState === "ready" ? (
              <div className="admin-recording-list">
                {adminRecordings.map((recording) => (
                  <div className="admin-recording-row" key={recording.recording_id}>
                    <div>
                      <strong>Visitor message</strong>
                      <p>
                        {recording.started_at_utc
                          ? new Date(recording.started_at_utc).toLocaleString()
                          : "Time unavailable"}
                        {recording.duration_s !== null && recording.duration_s !== undefined
                          ? ` · ${recording.duration_s.toFixed(1)} seconds`
                          : ""}
                      </p>
                      <p>Consent: {recording.consent_context ?? "unavailable"}</p>
                    </div>
                    <div className="admin-recording-actions">
                      <AdminVideoMessagePlayer
                        recordingId={recording.recording_id}
                        token={adminToken}
                      />
                      <button
                        className="delete-recording-btn"
                        onClick={() => handleDeleteRecording(recording.recording_id)}
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : null}
          </section>

          <section className="admin-gauges-section">
            <h3 className="section-title">Storage Status</h3>
            {!storageStatusKnown ? (
              <p className="admin-unavailable">Storage telemetry has not arrived from door-media.</p>
            ) : <div className="admin-gauges-grid">
              <Gauge
                title="SSD Space"
                value={freeGb.toFixed(1)}
                max={48}
                unit="GiB free"
                percentage={freePct}
                severity={ssdSeverity}
              />
              <Gauge
                title="Sync Queue Depth"
                value={storageStatus.queue_depth}
                max={20}
                unit="clips"
                severity={queueSeverity}
              />
              <Gauge
                title="Oldest Unsynced"
                value={oldestHrs.toFixed(1)}
                max={168}
                unit="hours"
                severity={oldestSeverity}
              />
            </div>}
          </section>

          <section className="admin-recordings-section">
            <h3 className="section-title">Local Recordings</h3>
            
            <div className="table-controls">
              <div className="control-group">
                <label htmlFor="kind-filter">Kind:</label>
                <select
                  id="kind-filter"
                  value={kindFilter}
                  onChange={(e) => { setKindFilter(e.target.value); setCurrentCursor(""); setCursorStack([]); }}
                >
                  <option value="all">All Kinds</option>
                  <option value="bell_clip">Bell Clip</option>
                  <option value="video_message">Video Message</option>
                  <option value="photo_booth">Photo Booth</option>
                </select>
              </div>

              <div className="control-group">
                <label htmlFor="sync-filter">Sync Status:</label>
                <select
                  id="sync-filter"
                  value={syncFilter}
                  onChange={(e) => { setSyncFilter(e.target.value); setCurrentCursor(""); setCursorStack([]); }}
                >
                  <option value="all">All States</option>
                  <option value="pending">Pending</option>
                  <option value="synced">Synced</option>
                </select>
              </div>

              <div className="control-group">
                <label htmlFor="page-size">Page Size:</label>
                <select
                  id="page-size"
                  value={pageSize}
                  onChange={(e) => { setPageSize(Number(e.target.value)); setCurrentCursor(""); setCursorStack([]); }}
                >
                  <option value={5}>5 per page</option>
                  <option value={10}>10 per page</option>
                  <option value={20}>20 per page</option>
                </select>
              </div>
            </div>

            {loading ? (
              <div className="loading-state">Loading recordings...</div>
            ) : recordings.length === 0 ? (
              <div className="empty-state">No recordings found matching filters.</div>
            ) : (
              <div className="table-responsive">
                <table className="recordings-table">
                  <thead>
                    <tr>
                      <th>Thumbnail</th>
                      <th>Recording ID</th>
                      <th>Kind</th>
                      <th>Started At (UTC)</th>
                      <th>Duration</th>
                      <th>Size</th>
                      <th>Sync Status</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recordings.map((rec) => {
                      const thumbSrc = rec.thumbnail_path
                        ? `${MEDIA_API_BASE}/${rec.thumbnail_path}`
                        : null;
                      const sizeKb = rec.size_bytes ? (rec.size_bytes / 1024).toFixed(1) : "0";
                      
                      return (
                        <tr key={rec.recording_id}>
                          <td>
                            {thumbSrc ? (
                              <img
                                src={thumbSrc}
                                alt="Clip thumbnail"
                                className="recording-thumb-preview"
                              />
                            ) : (
                              <div className="no-thumb-badge">No Thumb</div>
                            )}
                          </td>
                          <td className="mono-text" title={rec.recording_id}>
                            {rec.recording_id.substring(0, 8)}...
                          </td>
                          <td className="capitalize">{rec.kind.replace("_", " ")}</td>
                          <td>{rec.started_at_utc.replace("T", " ").substring(0, 19)}</td>
                          <td>{rec.duration_s ? `${rec.duration_s.toFixed(1)}s` : "-"}</td>
                          <td>{sizeKb} KB</td>
                          <td>
                            <span className={`sync-status-tag ${rec.sync_status}`}>
                              {rec.sync_status}
                            </span>
                          </td>
                          <td>
                            <button
                              className="delete-recording-btn"
                              onClick={() => handleDeleteRecording(rec.recording_id)}
                            >
                              Delete
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            <div className="pagination-controls">
              <button
                className="pagination-btn"
                onClick={handlePrevPage}
                disabled={cursorStack.length === 0}
              >
                &larr; Previous
              </button>
              <span className="pagination-info">
                Page {cursorStack.length + 1}
              </span>
              <button
                className="pagination-btn"
                onClick={handleNextPage}
                disabled={!nextCursor}
              >
                Next &rarr;
              </button>
            </div>
          </section>

          {FEATURE_PHOTOBOOTH && (
            <section className="admin-gallery-section">
              <h3 className="section-title">Photo Booth Gallery</h3>
              {galleryPhotos.length === 0 ? (
                <p className="desc">No saved photo booth stills awaiting review.</p>
              ) : (
                <div className="admin-gallery-grid">
                  {galleryPhotos.map((photo) => (
                    <div className="admin-gallery-row" key={photo.recording_id}>
                      <div className="gallery-photo-preview">
                        {photo.recording_id.slice(0, 2).toUpperCase()}
                      </div>
                      <div className="gallery-photo-detail">
                        <strong>{photo.recording_id.slice(0, 8)}</strong>
                        <p>Status: {photo.gallery_status}</p>
                        <p>Consent: {photo.consent_context ?? "visitor_initiated"}</p>
                        <p>Tags: {photo.tags.length > 0 ? photo.tags.join(", ") : "none"}</p>
                      </div>
                      <div className="gallery-photo-actions">
                        {photo.gallery_status !== "approved" && (
                          <button className="phrase-btn" onClick={() => approveGalleryPhoto(photo)}>
                            Approve
                          </button>
                        )}
                        {photo.gallery_status === "approved" && (
                          <>
                            <button className="phrase-btn" onClick={() => updateGalleryTags(photo)}>
                              Tags
                            </button>
                            <button
                              className="phrase-btn"
                              onClick={() => updateGalleryTags(photo, !photo.wallboard_moment)}
                            >
                              {photo.wallboard_moment ? "Hide Moment" : "Show Moment"}
                            </button>
                          </>
                        )}
                        <button className="delete-recording-btn" onClick={() => deleteGalleryPhoto(photo)}>
                          Delete
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}

          <AdminSocialPanel />
          <AdminEnrollmentPanel />
          <AdminAboutPanel />
        </div>
      </div>
    );
  };

  // --- NAVIGATION PAGE (DEFAULT ROOT /) ---
  const renderNavigation = () => {
    if (!DEV_TOOLS_ENABLED) {
      return renderWallboard();
    }

    return (
      <div className="navigation-view db-app-theme">
        <div className="nav-container">
          <header className="nav-header">
            <h1>Doorboard Kiosk SPA</h1>
            <p>Select a kiosk surface or route below to preview</p>
          </header>

          <div className="nav-links-grid">
            <div className="nav-card" onClick={() => navigateTo("/wallboard")}>
              <span className="nav-icon">🖥️</span>
              <h3>Wallboard</h3>
              <p>21.5" Public Ambient Display</p>
              <span className="route-tag">/wallboard</span>
            </div>

            <div className="nav-card" onClick={() => navigateTo("/doorpad")}>
              <span className="nav-icon">📱</span>
              <h3>DoorPad</h3>
              <p>7" Public Touchscreen Controller</p>
              <span className="route-tag">/doorpad</span>
            </div>

            <div className="nav-card" onClick={() => navigateTo("/visitor")}>
              <span className="nav-icon">🤳</span>
              <h3>Visitor Link</h3>
              <p>Mobile web page placeholder</p>
              <span className="route-tag">/visitor</span>
            </div>

            <div className="nav-card" onClick={() => navigateTo("/admin")}>
              <span className="nav-icon">🔑</span>
              <h3>Admin Console</h3>
              <p>Owner administration placeholder</p>
              <span className="route-tag">/admin</span>
            </div>

            <div className="nav-card" onClick={() => navigateTo("/live-view-demo")}>
              <span className="nav-icon">▣</span>
              <h3>Live View Demo</h3>
              <p>Reusable media preview surface</p>
              <span className="route-tag">/live-view-demo</span>
            </div>
          </div>
        </div>
      </div>
    );
  };

  return (
    <>
      {renderToast()}
      {route === "/wallboard" && renderWallboard()}
      {route === "/doorpad" && renderDoorPad()}
      {route === "/visitor" && renderVisitor()}
      {/* Secret owner-only "class year in review" reveal. Never linked from any
          UI; reachable only by typing /reveal#<owner-token>. */}
      {route === "/reveal" && <RevealPage />}
      {route === "/live-view-demo" && renderLiveViewDemo()}
      {(route === "/admin" || route === "/diagnostics") && renderAdmin()}
      {route === "/" && renderNavigation()}
      {(route === "/wallboard" || route === "/doorpad") && renderSimPanel()}
    </>
  );
}
