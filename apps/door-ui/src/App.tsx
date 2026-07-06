import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Tile,
  StatusBadge,
  BigButton,
  GreetingBanner,
  CountdownAutoReset,
  QRPlaceholder,
  SessionState,
  Gauge,
} from "@doorboard/ui-kit";
import { DoorboardEventClient, uuidv7 } from "@doorboard/event-client";
import type { DoorboardEvent } from "@doorboard/contracts";
import {
  presenceFixture,
  birdFixture,
  aircraftFixture,
  satelliteFixture,
  printerFixture,
  moodFixture,
  scoreboardFixture,
  foodFixture,
  pollFixture,
  guestbookFixture,
} from "./fixtures";

// Import CSS
import "@doorboard/ui-kit/index.css";
import "./App.css";

// Session states that trigger Wallboard Visitor Mode takeover
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

export function App() {
  const [route, setRoute] = useState<string>(window.location.pathname);
  const [sessionState, setSessionState] = useState<SessionState>("IDLE");
  const [activeProfile, setActiveProfile] = useState<string | null>(null);
  const [mockSessionId, setMockSessionId] = useState<string>(() => crypto.randomUUID());
  const [showSimPanel, setShowSimPanel] = useState<boolean>(true);
  const [currentTime, setCurrentTime] = useState<Date>(new Date());
  
  // DoorPad local state
  const [doorPadScreen, setDoorPadScreen] = useState<"home" | "ringing" | "message" | "guestbook" | "poll" | "checkin" | "privacy">("home");
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
      wsUrl: "ws://127.0.0.1:8765/ws",
      filters: ["session.*", "vision.*", "media.*"],
    });

    clientRef.current = client;

    // Listen to session state changes
    const unsubscribeSession = client.subscribe("session.state_changed", (event: DoorboardEvent) => {
      if (event && event.type === "session.state_changed" && event.payload) {
        const toState = event.payload.to_state;
        setSessionState(toState);

        if (toState === "IDLE") {
          setActiveProfile(null);
        }

        // Keep DoorPad local screen in sync
        if (toState === "IDLE") {
          setDoorPadScreen("home");
        } else if (VISITOR_STATES.includes(toState) && toState !== "SESSION_END") {
          setDoorPadScreen("ringing");
        }
      }
    });

    // Listen to vision identity stable events
    const unsubscribeVision = client.subscribe("vision.identity_stable", (event: DoorboardEvent) => {
      if (event && event.type === "vision.identity_stable" && event.payload) {
        setActiveProfile(event.payload.profile_id);
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

  // Show temporary toast feedback
  const triggerToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 3000);
  };

  const navigateTo = (path: string) => {
    window.history.pushState(null, "", path);
    setRoute(path);
  };

  const API_BASE = window.location.port === "5173" ? "http://127.0.0.1:8082" : "";

  const fetchRecordings = useCallback(async (cursorVal = currentCursor) => {
    setLoading(true);
    try {
      let url = `${API_BASE}/recordings?limit=${pageSize}`;
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
  }, [currentCursor, pageSize, kindFilter, syncFilter, API_BASE]);

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
      const res = await fetch(`${API_BASE}/recordings/${recordingId}`, {
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
    const isVisitorMode = VISITOR_STATES.includes(sessionState);

    if (isVisitorMode) {
      // VISITOR MODE TAKEOVER
      let greetTitle = "Hello, Visitor!";
      let greetSubtitle = "Press the bell or leave a video message below.";
      
      if (activeProfile === "owner") {
        greetTitle = "Welcome home, Taylor!";
        greetSubtitle = "You are currently marked as Available.";
      } else if (activeProfile === "roommate") {
        greetTitle = "Welcome home, Alex!";
        greetSubtitle = "You are currently marked as Busy.";
      }

      return (
        <CountdownAutoReset onReset={handleReset} timeoutMs={30000}>
          <div className="wallboard-visitor-view db-app-theme fade-in">
            <GreetingBanner
              title={greetTitle}
              subtitle={greetSubtitle}
              profileId={activeProfile}
            />
            
            <div className="visitor-grid">
              <Tile title="DoorPad Instructions">
                <div className="instructions-card">
                  <p>Touch the <strong>7" DoorPad screen</strong> below to:</p>
                  <ul>
                    <li>Ring the doorbell again</li>
                    <li>Leave a short video message</li>
                    <li>Sign our digital guestbook</li>
                    <li>Answer the roommate poll</li>
                  </ul>
                </div>
              </Tile>

              <Tile title="Availability State">
                <div className="status-display-box">
                  <div className="status-display-row">
                    <span className="person-name">Taylor (Owner)</span>
                    <StatusBadge label={presenceFixture.owner.label} />
                  </div>
                  <div className="status-display-row">
                    <span className="person-name">Alex (Roommate)</span>
                    <StatusBadge label={presenceFixture.roommate.label} />
                  </div>
                </div>
              </Tile>

              <Tile title="Scan Visitor QR">
                <QRPlaceholder url="http://door.local/visitor?token=visitor_kiosk_pass" />
              </Tile>
            </div>

            <div className="visitor-mode-footer">
              <BigButton variant="primary" onClick={handleReset}>
                Done / End Session
              </BigButton>
            </div>
          </div>
        </CountdownAutoReset>
      );
    }

    // AMBIENT MODE - TILE DASHBOARD
    return (
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

          {/* Tile 9: Room Poll */}
          <Tile title="Active Room Poll">
            <div className="poll-tile-content">
              <p className="poll-q"><strong>{pollFixture.question}</strong></p>
              {pollFixture.options.map((opt) => (
                <div key={opt.id} className="poll-option-row">
                  <span>{opt.text}</span>
                  <span><strong>{opt.votes}</strong> votes</span>
                </div>
              ))}
            </div>
          </Tile>

          {/* Tile 10: Guestbook Highlights */}
          <Tile title="Guestbook Highlights" asOf={guestbookFixture.occurred_at}>
            <div className="guestbook-tile-content">
              {guestbookFixture.entries.map((e, idx) => (
                <blockquote key={idx} className="guestbook-quote">
                  <p>"{e.text}"</p>
                  <cite>— {e.author}</cite>
                </blockquote>
              ))}
            </div>
          </Tile>
        </main>
      </div>
    );
  };

  // --- DOORPAD SURFACE ---
  const renderDoorPad = () => {
    const handleActionClick = (actionName: string, targetScreen: typeof doorPadScreen) => {
      if (targetScreen === "ringing") {
        triggerEvent("VISITOR_MODE");
        setDoorPadScreen("ringing");
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
            <BigButton
              id="btn-ring"
              variant="primary"
              icon={<span>🔔</span>}
              onClick={() => handleActionClick("Ring Bell", "ringing")}
            >
              Ring Bell
            </BigButton>
            
            <BigButton
              id="btn-video"
              icon={<span>📹</span>}
              onClick={() => handleActionClick("Video Message", "message")}
            >
              Video Message
            </BigButton>

            <BigButton
              id="btn-guestbook"
              icon={<span>✍️</span>}
              onClick={() => handleActionClick("Guestbook", "guestbook")}
            >
              Guestbook
            </BigButton>

            <BigButton
              id="btn-poll"
              icon={<span>📊</span>}
              onClick={() => handleActionClick("Poll Vote", "poll")}
            >
              Vote in Poll
            </BigButton>

            <BigButton
              id="btn-checkin"
              icon={<span>✅</span>}
              onClick={() => handleActionClick("Check In", "checkin")}
            >
              Visitor Check-In
            </BigButton>

            <BigButton
              id="btn-privacy"
              icon={<span>🔒</span>}
              onClick={() => handleActionClick("Privacy Notice", "privacy")}
            >
              Privacy & Info
            </BigButton>
          </div>
        </div>
      );
    }

    // SUB-SCREENS (Ringing/Active Session, Video Message, Guestbook, Poll, etc.)
    return (
      <CountdownAutoReset onReset={handleReset} timeoutMs={30000}>
        <div className="doorpad-view db-app-theme fade-in">
          {doorPadScreen === "ringing" && (
            <div className="doorpad-sub-content">
              <div className="pulse-ring-icon">🔔</div>
              <h2>Ringing Doorbell...</h2>
              <p>Wallboard has flipped to Visitor Mode. Taylor & Alex have been notified.</p>
              <div className="action-button-group">
                <BigButton variant="primary" onClick={handleReset}>
                  Cancel / End Session
                </BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "message" && (
            <div className="doorpad-sub-content">
              <h2>Leave a Video Message</h2>
              <p className="placeholder-subtext">[Placeholder Flow: Countdown → Record → Save]</p>
              <div className="video-booth-mock">
                <div className="mock-camera-lens">🔴 Recording Simulator</div>
              </div>
              <div className="action-button-group">
                <BigButton variant="primary" onClick={() => { triggerToast("Message Saved!"); handleReset(); }}>
                  Save Message
                </BigButton>
                <BigButton onClick={() => setDoorPadScreen("home")}>
                  Cancel
                </BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "guestbook" && (
            <div className="doorpad-sub-content">
              <h2>Leave a Guestbook Note</h2>
              <p className="placeholder-subtext">[Placeholder Flow: Select phrase or write note]</p>
              <div className="phrase-grid">
                <button className="phrase-btn" onClick={() => triggerToast('Selected: "Hey, stopped by!"')}>"Hey, stopped by!"</button>
                <button className="phrase-btn" onClick={() => triggerToast('Selected: "Call me later!"')}>"Call me later!"</button>
                <button className="phrase-btn" onClick={() => triggerToast('Selected: "Awesome door board!"')}>"Awesome door board!"</button>
              </div>
              <div className="action-button-group">
                <BigButton variant="primary" onClick={() => { triggerToast("Note Submitted!"); handleReset(); }}>
                  Submit
                </BigButton>
                <BigButton onClick={() => setDoorPadScreen("home")}>
                  Cancel
                </BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "poll" && (
            <div className="doorpad-sub-content">
              <h2>Vote in Poll</h2>
              <p className="poll-q"><strong>{pollFixture.question}</strong></p>
              <div className="poll-choices">
                {pollFixture.options.map((opt) => (
                  <button key={opt.id} className="phrase-btn" style={{ width: "100%", margin: "4px 0" }} onClick={() => triggerToast(`Voted for: ${opt.text}`)}>
                    {opt.text}
                  </button>
                ))}
              </div>
              <div className="action-button-group">
                <BigButton variant="primary" onClick={() => { triggerToast("Vote cast!"); handleReset(); }}>
                  Submit Vote
                </BigButton>
                <BigButton onClick={() => setDoorPadScreen("home")}>
                  Cancel
                </BigButton>
              </div>
            </div>
          )}

          {doorPadScreen === "checkin" && (
            <div className="doorpad-sub-content">
              <h2>Check In</h2>
              <p>Voluntarily mark yourself as a visitor to increment stats!</p>
              <div className="phrase-grid">
                <button className="phrase-btn" onClick={() => triggerToast("Checked in as Enrolled Visitor")}>Enrolled Visitor</button>
                <button className="phrase-btn" onClick={() => triggerToast("Checked in as Guest")}>Anonymous Guest</button>
              </div>
              <div className="action-button-group">
                <BigButton onClick={() => setDoorPadScreen("home")}>
                  Back
                </BigButton>
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
              <div className="action-button-group">
                <BigButton variant="primary" onClick={() => { triggerToast("Deletion Requested"); handleReset(); }}>
                  Request Deletion of My Data
                </BigButton>
                <BigButton onClick={() => setDoorPadScreen("home")}>
                  Back
                </BigButton>
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
          <div className="placeholder-badge">Visitor Screen Placeholder</div>
          <p className="desc">This is the tokenized mobile experience reachable via QR code scan.</p>
          <button className="back-home-btn" onClick={() => navigateTo("/")}>Back to Navigation</button>
        </div>
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
                        ? `${API_BASE}/${rec.thumbnail_path}`
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
      {(route === "/admin" || route === "/diagnostics") && renderAdmin()}
      {route === "/" && renderNavigation()}
      {(route === "/wallboard" || route === "/doorpad") && renderSimPanel()}
    </>
  );
}
