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
import type { DoorboardEvent } from "@doorboard/contracts";
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
} from "./fixtures";
import { socialApi, ApiError } from "./socialApi";
import type { GuestbookEntry, Poll, PollResultRow } from "./socialApi";
import { AdminSocialPanel } from "./AdminSocialPanel";
import { AdminEnrollmentPanel } from "./AdminEnrollmentPanel";
import { VisitorPage } from "./VisitorPage";
import { GuestbookQuote, PollOptionRow } from "./SocialRenderers";

// Import CSS
import "@doorboard/ui-kit/index.css";
import "./App.css";

const CANNED_GUESTBOOK_PHRASES = ["Hey, stopped by!", "Call me later!", "Awesome door board!"];

// Content this kiosk session created, so the Privacy screen can offer a
// real deletion path without needing a login/identity system.
type MyContentRef = { kind: "guestbook" | "checkin"; id: string; label: string };
const MY_CONTENT_KEY = "doorboard_my_social_content";

function loadMyContent(): MyContentRef[] {
  try {
    const raw = window.localStorage.getItem(MY_CONTENT_KEY);
    return raw ? (JSON.parse(raw) as MyContentRef[]) : [];
  } catch {
    return [];
  }
}

function saveMyContent(items: MyContentRef[]): void {
  window.localStorage.setItem(MY_CONTENT_KEY, JSON.stringify(items));
}

function apiErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return "Please slow down — too many submissions. Try again in a bit.";
    return err.message || fallback;
  }
  return fallback;
}

// Session states that trigger Wallboard Visitor Mode takeover
const API_BASE = import.meta.env.VITE_DOOR_API_BASE_URL ?? "http://127.0.0.1:8000";

const wsUrlFromApiBase = (base: string) => `${base.replace(/^http/, "ws")}/ws`;

type DoorPadScreen = "home" | "ringing" | "message" | "guestbook" | "poll" | "checkin" | "privacy";
type VideoStep = "offer" | "countdown" | "recording" | "review" | "saved" | "qr";

interface DoorApiSnapshot {
  session?: { state?: SessionState; session_id?: string | null };
  config?: { max_recording_s?: number };
}

interface VideoRecording {
  recording_id: string;
  path: string | null;
  consent_context: "visitor_initiated" | "bell_event" | null;
  thumbnail_path: string | null;
  playback_url?: string;
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
  const [showSimPanel, setShowSimPanel] = useState<boolean>(true);
  const [currentTime, setCurrentTime] = useState<Date>(new Date());
  
  // DoorPad local state
  const [doorPadScreen, setDoorPadScreen] = useState<DoorPadScreen>("home");
  const [videoStep, setVideoStep] = useState<VideoStep>("offer");
  const [countdown, setCountdown] = useState<number>(3);
  const [recordingElapsed, setRecordingElapsed] = useState<number>(0);
  const [maxRecordingS, setMaxRecordingS] = useState<number>(60);
  const [latestRecording, setLatestRecording] = useState<VideoRecording | null>(null);
  const [visitorQrUrl, setVisitorQrUrl] = useState<string | null>(null);
  const [adminRecordings, setAdminRecordings] = useState<VideoRecording[]>([]);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Admin surface media & storage states
  const [storageStatus, setStorageStatus] = useState({
    free_bytes: 48 * 1024 * 1024 * 1024 * 0.84, // 84% free of 48 GiB default
    queue_depth: 0,
    oldest_unsynced_s: 0,
    recording_allowed: true,
  });

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
  const [guestbookSubmitting, setGuestbookSubmitting] = useState<boolean>(false);
  const [currentPoll, setCurrentPoll] = useState<Poll | null>(null);
  const [pollResults, setPollResults] = useState<PollResultRow[] | null>(null);
  const [pollVoteError, setPollVoteError] = useState<string | null>(null);
  const [checkinSubmitting, setCheckinSubmitting] = useState<boolean>(false);
  const [myContent, setMyContent] = useState<MyContentRef[]>(() => loadMyContent());
  const [approvedGuestbook, setApprovedGuestbook] = useState<GuestbookEntry[]>([]);

  const clientRef = useRef<DoorboardEventClient | null>(null);

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

  // Initialize event client
  useEffect(() => {
    const client = new DoorboardEventClient({
      // Connect to simulator ws, or fallback to mock BroadcastChannel
      wsUrl: wsUrlFromApiBase(API_BASE),
      filters: ["session.*", "vision.*", "door.*", "media.*"],
      onSnapshot: (snapshot) => {
        const state = (snapshot as { state?: SessionState }).state;
        if (state) {
          setSessionState(state);
          if (state === "IDLE") {
            setDoorPadScreen("home");
          }
        }
      },
    });

    clientRef.current = client;

    // Listen to session state changes
    const unsubscribeSession = client.subscribe("session.state_changed", (event: DoorboardEvent) => {
      if (event && event.type === "session.state_changed" && event.payload) {
        const toState = event.payload.to_state;
        setSessionState(toState);

        if (toState === "IDLE") {
          setActiveProfile(null);
          setActiveDisplayName(null);
        }

        // Keep DoorPad local screen in sync, including sessions started by hardware.
        if (toState === "IDLE") {
          setDoorPadScreen("home");
          setVideoStep("offer");
        } else if (toState === "VIDEO_MESSAGE_OFFERED") {
          setDoorPadScreen("message");
          setVideoStep("offer");
        } else if (toState === "VIDEO_MESSAGE_RECORDING") {
          setDoorPadScreen("message");
          setVideoStep("recording");
        } else if (toState === "VIDEO_MESSAGE_REVIEW") {
          setDoorPadScreen("message");
          setVideoStep("review");
        } else if (toState === "VIDEO_MESSAGE_SAVED") {
          setDoorPadScreen("message");
          setVideoStep("saved");
        } else if (VISITOR_STATES.includes(toState)) {
          setDoorPadScreen("ringing");
        }
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
      }
    });

    return () => {
      unsubscribeSession();
      unsubscribeVision();
      unsubscribeMedia();
      client.close();
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE}/session`, { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : null))
      .then((data: DoorApiSnapshot | null) => {
        if (data?.session?.state) {
          setSessionState(data.session.state);
        }
        if (typeof data?.config?.max_recording_s === "number") {
          setMaxRecordingS(data.config.max_recording_s);
        }
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (route !== "/admin" && route !== "/diagnostics") return undefined;
    const controller = new AbortController();
    fetch(`${API_BASE}/admin/media-inbox`, { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : { recordings: [] }))
      .then((data: { recordings?: VideoRecording[] }) => {
        setAdminRecordings(data.recordings ?? []);
      })
      .catch(() => setAdminRecordings([]));
    return () => controller.abort();
  }, [route]);

  // Show temporary toast feedback
  const triggerToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 3000);
  };

  const navigateTo = (path: string) => {
    window.history.pushState(null, "", path);
    setRoute(path);
  };

  const MEDIA_API_BASE = window.location.port === "5173" ? "http://127.0.0.1:8082" : "";

  const fetchRecordings = useCallback(async (cursorVal = currentCursor) => {
    setLoading(true);
    try {
      let url = `${MEDIA_API_BASE}/recordings?limit=${pageSize}`;
      if (kindFilter !== "all") {
        url += `&kind=${kindFilter}`;
      }
      if (syncFilter !== "all") {
        url += `&sync_status=${syncFilter}`;
      }
      if (cursorVal) {
        url += `&cursor=${encodeURIComponent(cursorVal)}`;
      }
      
      const res = await fetch(url);
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
  }, [currentCursor, pageSize, kindFilter, syncFilter, MEDIA_API_BASE]);

  useEffect(() => {
    if (route === "/admin" || route === "/diagnostics") {
      fetchRecordings(currentCursor);
    }
  }, [route, currentCursor, fetchRecordings]);

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
      const res = await fetch(`${MEDIA_API_BASE}/recordings/${recordingId}`, {
        method: "DELETE",
      });
      if (res.ok) {
        triggerToast("Recording deleted");
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

  // Feed the ambient Guestbook Highlights + Room Poll tiles from real data.
  useEffect(() => {
    if (route !== "/wallboard") return;
    let cancelled = false;
    const load = () => {
      socialApi
        .listGuestbook(5)
        .then((entries) => {
          if (!cancelled) setApprovedGuestbook(entries);
        })
        .catch(() => {
          // NUC/door-api unreachable — tile just keeps showing last-known data.
        });
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
        .catch(() => {
          // Same fallback as above — ambient tile keeps last-known data.
        });
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
    const next = [...myContent, ref];
    setMyContent(next);
    saveMyContent(next);
  };

  const handleGuestbookSubmit = async (text: string) => {
    if (guestbookSubmitting) return;
    setGuestbookSubmitting(true);
    try {
      const entry = await socialApi.createGuestbookEntry(text, null);
      rememberMyContent({ kind: "guestbook", id: entry.id, label: text.slice(0, 40) });
      triggerToast("Note submitted! It'll show up once approved.");
      setGuestbookText("");
      handleReset();
    } catch (err) {
      triggerToast(apiErrorMessage(err, "Couldn't submit your note — try again."));
    } finally {
      setGuestbookSubmitting(false);
    }
  };

  const handlePollVote = async (optionId: string) => {
    if (!currentPoll) return;
    setPollVoteError(null);
    try {
      await socialApi.castVote(currentPoll.id, optionId);
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
      const label = kind === "enrolled" ? "Enrolled Visitor" : "Guest";
      // door-api derives attribution server-side from the session's cached
      // identity — this client never asserts a person_id.
      const checkin = await socialApi.createCheckin(label);
      rememberMyContent({ kind: "checkin", id: checkin.id, label });
      triggerToast(
        checkin.person_id
          ? `Checked in — recognized as ${checkin.person_id}!`
          : `Checked in as ${label}`
      );
      setDoorPadScreen("home");
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
      saveMyContent(next);
      triggerToast("Deletion request honored.");
    } catch (err) {
      triggerToast(apiErrorMessage(err, "Couldn't process the deletion request."));
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

  // Auto-reset when DoorPad or Wallboard is idle / timed out
  const handleReset = () => {
    triggerEvent("IDLE");
  };

  // The Wallboard's own "Done" button ends the visitor session properly (VISITOR_MODE
  // family -> SESSION_END) rather than jumping straight to IDLE, so the session-end
  // thank-you screen has a moment to show — mirroring door-api's real transition table.
  const endVisitorSession = () => {
    triggerEvent("SESSION_END");
  };

  // door-api auto-expires SESSION_END -> IDLE; this mock mirrors that so the thank-you
  // screen the Wallboard shows during SESSION_END returns to ambient on its own.
  useEffect(() => {
    if (sessionState !== "SESSION_END") return;
    const timeout = setTimeout(() => triggerEvent("IDLE"), 3000);
    return () => clearTimeout(timeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionState]);

  // Render Simulator panel overlay (for interactive dev)
  const renderSimPanel = () => {
    if (!showSimPanel) {
      return (
        <button className="sim-panel-toggle-btn" onClick={() => setShowSimPanel(true)}>
          ⚙️ Open Sim Control
        </button>
      );
    }

    return (
      <div className="sim-panel-overlay">
        <div className="sim-panel-header">
          <h3>Simulation Panel</h3>
          <button onClick={() => setShowSimPanel(false)}>❌ Close</button>
        </div>
        <div className="sim-panel-body">
          <p>Current Session State: <strong>{sessionState}</strong></p>
          <div className="sim-panel-buttons">
            <button onClick={() => triggerEvent("VISITOR_MODE", null)}>🔔 Press Bell (Generic)</button>
            <button onClick={() => triggerEvent("VISITOR_MODE", "owner")}>👤 Recognize Owner (Taylor)</button>
            <button onClick={() => triggerEvent("VISITOR_MODE", "roommate")}>👥 Recognize Roommate (Alex)</button>
            <button onClick={() => handleReset()}>🔄 Reset to IDLE</button>
          </div>
        </div>
      </div>
    );
  };

  // Toast Component
  const renderToast = () => {
    if (!toastMessage) return null;
    return <div className="db-toast-message">{toastMessage}</div>;
  };

  // --- WALLBOARD SURFACE ---
  const renderWallboard = () => {
    const isVisitorMode = WALLBOARD_TAKEOVER_STATES.includes(sessionState);
    const visitorUrl = `${window.location.origin}/visitor?token=${mockSessionId}`;

    return (
      <CrossfadeSwitch activeKey={isVisitorMode ? "visitor" : "ambient"}>
        {isVisitorMode ? (
          <WallboardVisitorMode
            sessionState={sessionState}
            sessionId={mockSessionId}
            profileId={activeProfile}
            displayName={activeDisplayName}
            presence={presenceFixture}
            pollQuestion={currentPoll?.question ?? "No poll running right now."}
            visitorUrl={visitorUrl}
            onDone={endVisitorSession}
          />
        ) : (
          // AMBIENT MODE - TILE DASHBOARD
          <div className="wallboard-ambient-view db-app-theme">
            <header className="ambient-header">
              <div className="ambient-header-left">
                <h1 className="ambient-header-title">Room 304 Wallboard</h1>
                <span className="ambient-header-subtitle">Dorm Hallway Display</span>
              </div>
              <div className="ambient-clock">
                {currentTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
              </div>
            </header>

            <main className="ambient-grid">
              {/* Tile 1: Presence */}
              <Tile title="Presence" asOf={presenceFixture.owner.occurred_at}>
                <div className="presence-tile-content">
                  <div className="presence-row">
                    <span>Taylor:</span>
                    <StatusBadge label={presenceFixture.owner.label} />
                  </div>
                  <div className="presence-row">
                    <span>Alex:</span>
                    <StatusBadge label={presenceFixture.roommate.label} />
                  </div>
                </div>
              </Tile>

              {/* Tile 2: Mood */}
              <Tile title="Current Mood" asOf={moodFixture.occurred_at}>
                <div className="mood-tile-content">
                  <span className="mood-emoji">🎯</span>
                  <span className="mood-text">Taylor is feeling <strong>{moodFixture.mood}</strong></span>
                </div>
              </Tile>

              {/* Tile 3: Birds */}
              <Tile title="Bird Detections" asOf={birdFixture.occurred_at}>
                <div className="bird-tile-content">
                  <p className="bird-stat">Total today: <strong>{birdFixture.total_detections}</strong></p>
                  {birdFixture.top_species.map((s, idx) => (
                    <div key={idx} className="bird-row">
                      <span>{s.name} (x{s.count})</span>
                      <span className="bird-conf">{(s.confidence_avg * 100).toFixed(0)}% conf</span>
                    </div>
                  ))}
                </div>
              </Tile>

              {/* Tile 4: Aircraft */}
              <Tile title="Overhead Aircraft" asOf={aircraftFixture.occurred_at}>
                <div className="aircraft-tile-content">
                  {aircraftFixture.nearby.map((a, idx) => (
                    <div key={idx} className="aircraft-row">
                      <span className="aircraft-call">{a.callsign}</span>
                      <span>{a.altitude_ft.toLocaleString()} ft</span>
                      <span>{a.distance_km} km away</span>
                    </div>
                  ))}
                </div>
              </Tile>

              {/* Tile 5: Satellite Pass */}
              <Tile title="Next Satellite Pass" asOf={satelliteFixture.occurred_at}>
                <div className="satellite-tile-content">
                  <p>🛰️ <strong>{satelliteFixture.satellite}</strong></p>
                  <p>Rise: {new Date(satelliteFixture.rise_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</p>
                  <p>Direction: {satelliteFixture.direction} ({satelliteFixture.max_elevation_deg}° max elev)</p>
                </div>
              </Tile>

              {/* Tile 6: Printer Status */}
              <Tile title="3D Printer" asOf={printerFixture.occurred_at}>
                <div className="printer-tile-content">
                  <p>Job: <strong>{printerFixture.job_name}</strong> ({printerFixture.state})</p>
                  <div className="progress-bar-container">
                    <div className="progress-bar-fill" style={{ width: `${printerFixture.progress_pct}%` }} />
                  </div>
                  <p className="printer-subtext">{printerFixture.progress_pct}% completed · ETA 15m</p>
                </div>
              </Tile>

              {/* Tile 7: Roommate Scoreboard */}
              <Tile title="Scoreboard" asOf={scoreboardFixture.occurred_at}>
                <div className="scoreboard-tile-content">
                  {scoreboardFixture.scores.map((s, idx) => (
                    <div key={idx} className="score-row">
                      <span>{s.name}</span>
                      <span className="score-points"><strong>{s.score}</strong> pts</span>
                    </div>
                  ))}
                </div>
              </Tile>

              {/* Tile 8: Daily Food */}
              <Tile title="Daily Food Recommendation" asOf={foodFixture.occurred_at}>
                <div className="food-tile-content">
                  <h4>🍜 {foodFixture.title}</h4>
                  <p>{foodFixture.detail}</p>
                </div>
              </Tile>

              {/* Tile 9: Room Poll — fed by the real current poll (T-403) */}
              <Tile title="Active Room Poll">
                <div className="poll-tile-content">
                  {!currentPoll && <p>No poll running right now.</p>}
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
                  {approvedGuestbook.length === 0 && <p>No guestbook notes yet — be the first!</p>}
                  {approvedGuestbook.map((e) => (
                    <GuestbookQuote key={e.id} text={e.text} authorLabel={e.author_label} />
                  ))}
                </div>
              </Tile>
            </main>
          </div>
        )}
      </CrossfadeSwitch>
    );
  };

  // --- DOORPAD SURFACE ---
  const postDoorApi = useCallback(async (path: string) => {
    try {
      const response = await fetch(`${API_BASE}${path}`, { method: "POST" });
      if (!response.ok) {
        triggerToast("Local service unavailable");
        return null;
      }
      return response.json() as Promise<DoorApiSnapshot>;
    } catch {
      triggerToast("Local service unavailable");
      return null;
    }
  }, []);

  const fetchLatestRecording = useCallback(async () => {
    for (let attempt = 0; attempt < 12; attempt += 1) {
      try {
        const response = await fetch(`${API_BASE}/doorpad/video-message/latest`);
        if (response.ok) {
          const data = (await response.json()) as { recording?: VideoRecording | null };
          if (data.recording) {
            setLatestRecording(data.recording);
            return;
          }
        }
      } catch {
        // Retry briefly; final unavailable state is shown by the review screen.
      }
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
  }, []);

  const stopRecording = useCallback(async () => {
    setVideoStep("review");
    await postDoorApi("/doorpad/video-message/stop");
    void fetchLatestRecording();
  }, [fetchLatestRecording, postDoorApi]);

  const beginRecording = useCallback(async () => {
    setRecordingElapsed(0);
    setLatestRecording(null);
    setVideoStep("recording");
    await postDoorApi("/doorpad/video-message/start");
  }, [postDoorApi]);

  useEffect(() => {
    if (doorPadScreen !== "message" || videoStep !== "countdown") return undefined;
    if (countdown <= 0) {
      void beginRecording();
      return undefined;
    }
    const timer = window.setTimeout(() => setCountdown((value) => value - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [beginRecording, countdown, doorPadScreen, videoStep]);

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
    setDoorPadScreen("message");
    setVideoStep("countdown");
    setCountdown(3);
    setLatestRecording(null);
    await postDoorApi("/doorpad/video-message/offer");
  };

  const discardVideoFlow = async () => {
    setLatestRecording(null);
    setVisitorQrUrl(null);
    setDoorPadScreen("home");
    setVideoStep("offer");
    await postDoorApi("/doorpad/video-message/discard");
  };

  const saveVideoMessage = async () => {
    setVideoStep("saved");
    await postDoorApi("/doorpad/video-message/save");
  };

  const showVisitorQr = async () => {
    setDoorPadScreen("message");
    setVideoStep("qr");
    const offered = await postDoorApi("/doorpad/video-message/offer");
    if (offered?.config?.max_recording_s) {
      setMaxRecordingS(offered.config.max_recording_s);
    }
    try {
      const response = await fetch(`${API_BASE}/visitor-token`);
      if (response.ok) {
        const data = (await response.json()) as { url: string };
        setVisitorQrUrl(data.url);
      } else {
        triggerToast("QR token unavailable");
      }
    } catch {
      triggerToast("QR token unavailable");
    }
  };

  const ringDoorbell = () => {
    setSessionState("VISITOR_MODE");
    setDoorPadScreen("ringing");
    void postDoorApi("/doorpad/ring");
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
        <div className="doorpad-view db-app-theme">
          <header className="doorpad-header">
            <h2>Room 304 DoorPad</h2>
            <p>Tap an action below to interact</p>
          </header>
          
          <div className="doorpad-grid">
            <BigButton id="btn-ring" variant="primary" icon={<span>🔔</span>} onClick={ringDoorbell}>
              Ring Bell
            </BigButton>
            
            <BigButton id="btn-video" icon={<span>📹</span>} onClick={() => {
              setDoorPadScreen("message");
              setVideoStep("offer");
            }}>
              Video Message
            </BigButton>

            <BigButton id="btn-guestbook" icon={<span>✍️</span>} onClick={() => handleActionClick("Guestbook", "guestbook")}>
              Guestbook
            </BigButton>

            <BigButton id="btn-poll" icon={<span>📊</span>} onClick={() => handleActionClick("Poll Vote", "poll")}>
              Vote in Poll
            </BigButton>

            <BigButton id="btn-checkin" icon={<span>✅</span>} onClick={() => handleActionClick("Check In", "checkin")}>
              Visitor Check-In
            </BigButton>

            <BigButton id="btn-privacy" icon={<span>🔒</span>} onClick={() => handleActionClick("Privacy Notice", "privacy")}>
              Privacy & Info
            </BigButton>
          </div>
        </div>
      );
    }

    return (
      <CountdownAutoReset onReset={doorPadScreen === "message" ? discardVideoFlow : handleReset} timeoutMs={30000}>
        <div className="doorpad-view db-app-theme fade-in">
          {doorPadScreen === "ringing" && (
            <div className="doorpad-sub-content">
              <div className="pulse-ring-icon">🔔</div>
              <h2>Ringing Doorbell...</h2>
              <p>Wallboard has flipped to Visitor Mode. Taylor & Alex have been notified.</p>
              <div className="qr-inline">
                <BigButton onClick={showVisitorQr}>Show QR Handoff</BigButton>
              </div>
              <div className="action-button-group">
                <BigButton variant="primary" onClick={handleReset}>Cancel / End Session</BigButton>
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
                <BigButton onClick={showVisitorQr}>Use Phone QR</BigButton>
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
                <BigButton variant="primary" onClick={stopRecording}>Stop & Review</BigButton>
                <BigButton onClick={discardVideoFlow}>Discard</BigButton>
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
                <BigButton variant="primary" onClick={saveVideoMessage}>Save Message</BigButton>
                <BigButton onClick={startVideoFlow}>Re-record</BigButton>
                <BigButton onClick={discardVideoFlow}>Discard</BigButton>
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
              <h2>Continue on Phone</h2>
              {visitorQrUrl ? <QRPlaceholder url={visitorQrUrl} /> : <p>Preparing QR token...</p>}
              <div className="action-button-group">
                <BigButton variant="primary" onClick={startVideoFlow}>Record Here Instead</BigButton>
                <BigButton onClick={discardVideoFlow}>Cancel</BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "guestbook" && (
            <div className="doorpad-sub-content">
              <h2>Leave a Guestbook Note</h2>
              <p className="placeholder-subtext">Pick a phrase or write a short note (280 chars max)</p>
              <div className="phrase-grid">
                {CANNED_GUESTBOOK_PHRASES.map((phrase) => (
                  <button
                    key={phrase}
                    className="phrase-btn"
                    disabled={guestbookSubmitting}
                    onClick={() => handleGuestbookSubmit(phrase)}
                  >
                    "{phrase}"
                  </button>
                ))}
              </div>
              <textarea
                className="guestbook-freetext"
                maxLength={280}
                rows={3}
                placeholder="Or write your own note..."
                value={guestbookText}
                onChange={(e) => setGuestbookText(e.target.value)}
              />
              <div className="action-button-group">
                <BigButton variant="primary" onClick={() => { triggerToast("Note Submitted!"); handleReset(); }}>Submit</BigButton>
                <BigButton onClick={() => setDoorPadScreen("home")}>Cancel</BigButton>
              </div>
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
                          onClick={() => handlePollVote(opt.id)}
                        >
                          {opt.text}
                          {result !== undefined && (
                            <span className="poll-vote-count"> — {result.votes} votes</span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                </>
              )}
              <div className="action-button-group">
                <BigButton variant="primary" onClick={() => { triggerToast("Vote cast!"); handleReset(); }}>Submit Vote</BigButton>
                <BigButton onClick={() => setDoorPadScreen("home")}>Cancel</BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "checkin" && (
            <div className="doorpad-sub-content">
              <h2>Check In</h2>
              <p>Voluntarily mark yourself as a visitor to increment stats!</p>
              <div className="phrase-grid">
                <button
                  className="phrase-btn"
                  disabled={checkinSubmitting}
                  onClick={() => handleCheckin("enrolled")}
                >
                  Enrolled Visitor
                </button>
                <button
                  className="phrase-btn"
                  disabled={checkinSubmitting}
                  onClick={() => handleCheckin("guest")}
                >
                  Anonymous Guest
                </button>
              </div>
              <div className="action-button-group">
                <BigButton onClick={() => setDoorPadScreen("home")}>Back</BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "privacy" && (
            <div className="doorpad-sub-content">
              <h2>Camera Notice & Deletion Requests</h2>
              <div className="privacy-info-box">
                <p>📸 This door pad utilizes physical cameras for proactive face recognition of enrolled users.</p>
                <p>🔒 Embeddings for unknown visitors are never persisted or recorded. biometrics stay strictly offline on device.</p>
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
                <BigButton variant="primary" onClick={() => { triggerToast("Deletion Requested"); handleReset(); }}>Request Deletion of My Data</BigButton>
                <BigButton onClick={() => setDoorPadScreen("home")}>Back</BigButton>
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
      <div className="placeholder-view db-app-theme">
        <div className="placeholder-card">
          <h1>Visitor Surface</h1>
          <p className="subtitle-tag">Route: `/visitor`</p>
          <VisitorPage sessionState={sessionState} />
          <button className="back-home-btn" onClick={() => navigateTo("/")}>Back to Navigation</button>
        </div>
      </div>
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

  // --- ADMIN SURFACE ---
  const renderAdmin = () => {
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
              <p className="subtitle-tag">Route: `/admin` | `/diagnostics`</p>
            </div>
            <button className="back-home-btn" onClick={() => navigateTo("/")}>Back to Navigation</button>
          </header>

          <LiveVideoPreview title="Local Live View" showStats />
          <div className="admin-stats-grid">
            <div className="stat-card"><span>Uptime:</span> <strong>3d 12h</strong></div>
            <div className="stat-card"><span>SQLite WAL:</span> <strong>Active</strong></div>
            <div className="stat-card"><span>Hailo NPU:</span> <strong>Degraded (Sim)</strong></div>
            <div className="stat-card"><span>SSD Space:</span> <strong>84% free</strong></div>
          </div>

          <section className="admin-inbox-section">
            <h2>Video Message Inbox</h2>
            {adminRecordings.length === 0 ? (
              <p className="desc">No saved visitor video messages.</p>
            ) : (
              <div className="admin-recording-list">
                {adminRecordings.map((recording) => (
                  <div className="admin-recording-row" key={recording.recording_id}>
                    <div>
                      <strong>{recording.recording_id.slice(0, 8)}</strong>
                      <p>Consent: {recording.consent_context}</p>
                      <p>Clip: {recording.path}</p>
                    </div>
                    <span>{recording.thumbnail_path ? "Thumbnail ready" : "No thumbnail"}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="admin-gauges-section">
            <h3 className="section-title">Storage Status</h3>
            <div className="admin-gauges-grid">
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
            </div>
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

          <AdminSocialPanel />
          <AdminEnrollmentPanel />
        </div>
      </div>
    );
  };

  // --- NAVIGATION PAGE (DEFAULT ROOT /) ---
  const renderNavigation = () => {
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
      {route === "/live-view-demo" && renderLiveViewDemo()}
      {(route === "/admin" || route === "/diagnostics") && renderAdmin()}
      {route === "/" && renderNavigation()}
      {(route === "/wallboard" || route === "/doorpad") && renderSimPanel()}
    </>
  );
}
