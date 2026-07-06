import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Tile,
  StatusBadge,
  BigButton,
  GreetingBanner,
  CountdownAutoReset,
  QRPlaceholder,
  SessionState,
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

export function App() {
  const [route, setRoute] = useState<string>(window.location.pathname);
  const [sessionState, setSessionState] = useState<SessionState>("IDLE");
  const [activeProfile, setActiveProfile] = useState<string | null>(null);
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
      filters: ["session.*", "vision.*", "door.*"],
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
      }
    });

    return () => {
      unsubscribeSession();
      unsubscribeVision();
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

  const renderVideoPreview = () => (
    <div className="video-preview-frame" aria-label="Live self-preview">
      <div className="video-preview-scan" />
      <div className="video-preview-label">Live Preview</div>
    </div>
  );

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
              <p className="placeholder-subtext">[Placeholder Flow: Select phrase or write note]</p>
              <div className="phrase-grid">
                <button className="phrase-btn" onClick={() => triggerToast('Selected: "Hey, stopped by!"')}>"Hey, stopped by!"</button>
                <button className="phrase-btn" onClick={() => triggerToast('Selected: "Call me later!"')}>"Call me later!"</button>
                <button className="phrase-btn" onClick={() => triggerToast('Selected: "Awesome door board!"')}>"Awesome door board!"</button>
              </div>
              <div className="action-button-group">
                <BigButton variant="primary" onClick={() => { triggerToast("Note Submitted!"); handleReset(); }}>Submit</BigButton>
                <BigButton onClick={() => setDoorPadScreen("home")}>Cancel</BigButton>
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
                <button className="phrase-btn" onClick={() => triggerToast("Checked in as Enrolled Visitor")}>Enrolled Visitor</button>
                <button className="phrase-btn" onClick={() => triggerToast("Checked in as Guest")}>Anonymous Guest</button>
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
          <div className="placeholder-badge">Visitor Screen Placeholder</div>
          <p className="desc">This is the tokenized mobile experience reachable via QR code scan.</p>
          <button className="back-home-btn" onClick={() => navigateTo("/")}>Back to Navigation</button>
        </div>
      </div>
    );
  };

  // --- ADMIN SURFACE ---
  const renderAdmin = () => {
    return (
      <div className="placeholder-view db-app-theme">
        <div className="placeholder-card admin-inbox-card">
          <h1>Admin Control Panel</h1>
          <p className="subtitle-tag">Route: `/admin` | `/diagnostics`</p>
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
          <button className="back-home-btn" onClick={() => navigateTo("/")}>Back to Navigation</button>
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
