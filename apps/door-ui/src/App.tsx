import React, { useState, useEffect, useRef } from "react";
import {
  Tile,
  StatusBadge,
  BigButton,
  CountdownAutoReset,
  LiveVideoPreview,
  CrossfadeSwitch,
  SessionState,
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
  const [doorPadScreen, setDoorPadScreen] = useState<"home" | "ringing" | "message" | "guestbook" | "poll" | "checkin" | "privacy">("home");
  const [toastMessage, setToastMessage] = useState<string | null>(null);

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
      wsUrl: "ws://127.0.0.1:8765/ws",
      filters: ["session.*", "vision.*"],
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
        setActiveDisplayName(event.payload.display_name);
      }
    });

    return () => {
      unsubscribeSession();
      unsubscribeVision();
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
              <LiveVideoPreview title="Message Preview" />
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
                <BigButton
                  variant="primary"
                  disabled={guestbookSubmitting || guestbookText.trim().length === 0}
                  onClick={() => handleGuestbookSubmit(guestbookText)}
                >
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
                <BigButton onClick={() => setDoorPadScreen("home")}>
                  Back
                </BigButton>
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
    return (
      <div className="placeholder-view db-app-theme">
        <div className="placeholder-card admin-panel-card">
          <h1>Admin Control Panel</h1>
          <p className="subtitle-tag">Route: `/admin` | `/diagnostics`</p>
          <div className="placeholder-badge">Admin Screen Placeholder</div>
          <LiveVideoPreview title="Local Live View" showStats />
          <div className="admin-stats-grid">
            <div className="stat-card"><span>Uptime:</span> <strong>3d 12h</strong></div>
            <div className="stat-card"><span>SQLite WAL:</span> <strong>Active</strong></div>
            <div className="stat-card"><span>Hailo NPU:</span> <strong>Degraded (Sim)</strong></div>
            <div className="stat-card"><span>SSD Space:</span> <strong>84% free</strong></div>
          </div>
          <AdminSocialPanel />
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
