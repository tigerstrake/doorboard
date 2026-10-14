// ============================================================================
// FROZEN CONTRACTS — the single source of truth shared by every module.
//
// Owned by the orchestrator. Agents must NOT edit this file. If you need a
// change, implement against what is here and propose the change in your final
// report. Read MASTERPLAN.md for the semantics behind each type.
// ============================================================================

// ---------------------------------------------------------------------------
// Basics
// ---------------------------------------------------------------------------
export type PlayerId = 0 | 1;
export type Lane = -1 | 0 | 1;
export type PlayerCount = 1 | 2;
export type Quality = 'low' | 'high';
export type PoseModel = 'lite' | 'full';

// ---------------------------------------------------------------------------
// Player actions (any input source → game simulation)
// ---------------------------------------------------------------------------
export type PlayerAction =
  /** Absolute lane, from body position (pose input). */
  | { type: 'lane'; lane: Lane }
  /** Relative lane change (keyboard / autopilot). */
  | { type: 'move'; dir: -1 | 1 }
  | { type: 'jump' }
  /** Roll. While airborne: fast-fall, then roll on landing. */
  | { type: 'duck' };

export type InputSource = 'pose' | 'keyboard' | 'autopilot';

export interface ActionEvent {
  playerId: PlayerId;
  action: PlayerAction;
  source: InputSource;
  /** performance.now() in ms */
  t: number;
}

// ---------------------------------------------------------------------------
// Vision (camera + pose + gestures)
// ---------------------------------------------------------------------------

/**
 * Pose landmark in normalized image coordinates, in DISPLAY space:
 * when tuning.mirror is true (default), x is already mirrored (x = 1 - rawX),
 * so 0 = left edge of the screen as the player sees it, 1 = right edge.
 * y: 0 = top, 1 = bottom. 33 landmarks, MediaPipe BlazePose topology
 * (indices in src/shared/pose.ts).
 */
export interface Landmark {
  x: number;
  y: number;
  z: number;
  visibility: number;
}

export type PositioningHint =
  | 'too-close'
  | 'too-far'
  | 'move-left'
  | 'move-right'
  | 'head-cut-off'
  | 'low-visibility';

export interface TrackedPerson {
  slot: PlayerId;
  /** 33 smoothed landmarks in display space. */
  landmarks: Landmark[];
  /** Torso anchor (shoulder midpoint, blended toward hips when visible), display space. */
  center: { x: number; y: number };
  /** Body scale in normalized image-width units (≈ shoulder width). All thresholds are multiples of this. */
  scale: number;
  /** Both hands currently raised above the head. */
  handsUp: boolean;
  /** 0..1; reaches 1 after hands are held up for HANDS_UP_HOLD_MS. Falls back to 0 quickly when released. */
  handsUpProgress: number;
  hints: PositioningHint[];
  /** 0..1 mean visibility of the key torso landmarks. */
  confidence: number;
}

export type GesturePhase = 'uncalibrated' | 'calibrating' | 'ready' | 'airborne' | 'ducking';

/** Per-slot gesture detector state for overlays/debug. All coordinates normalized, display space. */
export interface GestureState {
  slot: PlayerId;
  phase: GesturePhase;
  /** 0..1 while calibrating; 1 when calibrated. */
  calibrationProgress: number;
  /** Lane implied by the body position right now (source of truth on start/resume). */
  lane: Lane;
  /** Calibrated lane-zone center. Zone boundaries are centerX ± laneHalfWidth. */
  centerX: number;
  laneHalfWidth: number;
  /** Neutral torso-anchor y. */
  baselineY: number;
  /** Anchor rising above this line (smaller y) ⇒ jump. */
  jumpLineY: number;
  /** Anchor dropping below this line (larger y) ⇒ duck. */
  duckLineY: number;
  lastGesture: { kind: 'jump' | 'duck' | 'lane'; t: number } | null;
}

export interface VisionFrame {
  /** performance.now() when the processed video frame was captured. */
  t: number;
  /** 0..2 tracked players, sorted by slot. */
  people: TrackedPerson[];
  /** People detected but not assigned to a slot (spectators). */
  extraPeople: number;
  /** Index = slot. null when that slot has no detector state yet. */
  gestures: [GestureState | null, GestureState | null];
  inferenceMs: number;
  /** Inference frames per second. */
  fps: number;
}

export type VisionStatus =
  | 'idle'
  | 'loading-model'
  | 'model-ready'
  | 'starting-camera'
  | 'running'
  | 'error';

export interface VisionTuning {
  /** Lateral offset (× body scale) from centerX needed to enter a side lane. */
  laneThreshold: number;
  /** (× body scale) how far back toward center before leaving a side lane (hysteresis). */
  laneHysteresis: number;
  /** Rise of the torso anchor (× body scale) that triggers a jump. */
  jumpThreshold: number;
  /** Drop of the torso anchor (× body scale) that triggers a duck. */
  duckThreshold: number;
  /** 0..1 — higher = smoother but laggier. */
  smoothing: number;
  /** Mirror the camera horizontally (selfie view). */
  mirror: boolean;
}

export interface IVisionSystem {
  readonly status: VisionStatus;
  readonly error: string | null;
  /** Load the WASM runtime + `models/pose_landmarker_${model}.task`. Call again to switch models. */
  loadModel(model: PoseModel): Promise<void>;
  /** Start (or switch) the camera. Resolves once the video is playing. Rejects on denial / no camera. */
  startCamera(deviceId?: string | null): Promise<void>;
  listCameras(): Promise<MediaDeviceInfo[]>;
  /** The <video> element with the live, UNMIRRORED camera stream (null before startCamera). */
  getVideo(): HTMLVideoElement | null;
  /** Number of player slots to track. Additional people are spectators. */
  setPlayerCount(n: PlayerCount): void;
  /**
   * Freeze slot identities. With `order`, the people currently in slots order[0..] become
   * slots 0.. (player count = order.length) and everyone else becomes a spectator — e.g.
   * lockSlots(true, [1]) makes the person in slot 1 the solo player (slot 0).
   * While unlocked, slots are re-assigned left→right every frame.
   */
  lockSlots(locked: boolean, order?: PlayerId[]): void;
  /** Begin capturing a neutral baseline for a slot (player stands still, hands down). */
  startCalibration(slot: PlayerId): void;
  /** Reset one slot's calibration, or all slots when omitted. */
  resetCalibration(slot?: PlayerId): void;
  /**
   * Call every animation frame. Runs inference when a new video frame is available,
   * updates gesture detectors and fires onAction callbacks synchronously.
   * Returns the latest frame (or null before the first result).
   */
  update(now: number): VisionFrame | null;
  /** Gesture actions (lane changes, jump, duck) for calibrated slots. Returns an unsubscribe fn. */
  onAction(cb: (e: ActionEvent) => void): () => void;
  getTuning(): VisionTuning;
  setTuning(t: Partial<VisionTuning>): void;
  /** Square JPEG data URL of the player's face/upper body from the latest frame (display space), or null. */
  captureSelfie(slot: PlayerId, size?: number): string | null;
  dispose(): void;
}

// ---------------------------------------------------------------------------
// Game simulation (pure logic, no DOM / Three.js)
// ---------------------------------------------------------------------------

/** Dimensions per kind live in src/shared/constants.ts (render & collision must agree). */
export type ObstacleKind = 'barrier_low' | 'barrier_high' | 'train' | 'ramp';

export interface ObstacleState {
  /** Unique within a run. */
  id: number;
  kind: ObstacleKind;
  lane: Lane;
  /** Track position (m) of the near end (closest to the player). */
  s: number;
  /** Extent along the track (m). Far end = s + length. */
  length: number;
  /** Trains only: true ⇒ driving toward the player. */
  moving: boolean;
  /** m/s toward the player (0 when static). */
  speed: number;
  /** Deterministic visual variant (train livery / colors). */
  variant: number;
}

/** jetpack: fly above the trains on a coin trail. slowmo: world runs at ~60% speed. star: invincible, smash through obstacles. coinRain: dense coin lines everywhere. */
export type PowerUpKind = 'magnet' | 'multiplier' | 'sneakers' | 'hoverboard' | 'jetpack' | 'slowmo' | 'star' | 'coinRain';
export type CollectibleKind = 'coin' | PowerUpKind;

export interface CollectibleState {
  id: number;
  kind: CollectibleKind;
  lane: Lane;
  /** Track position (m). */
  s: number;
  /** Lateral position (m) — laneX(lane) unless being pulled by a magnet. */
  x: number;
  /** Height of the item's center above the ground (m). */
  y: number;
}

export type PlayerAnim = 'run' | 'jump' | 'fall' | 'roll' | 'stumble' | 'dead';

export interface PlayerState {
  /** Lane the runner is in or moving to. */
  lane: Lane;
  /** Lateral position (m). */
  x: number;
  /** Feet height above the ground (m); > 0 when airborne or on a train roof. */
  y: number;
  /** Vertical velocity (m/s). */
  vy: number;
  /** On the ground or on a roof/ramp. */
  grounded: boolean;
  onRoof: boolean;
  anim: PlayerAnim;
  /** Seconds since the current anim started. */
  animTime: number;
  rolling: boolean;
  /** -1..1 lateral lean for animation (sign = direction of motion). */
  lean: number;
  /** A desired lane change currently blocked by a train alongside (-1 / 1), else 0. */
  blockedDir: -1 | 0 | 1;
  /** Hoverboard (one-crash shield) active. */
  hoverboard: boolean;
  /** Seconds of post-hoverboard-break invulnerability left (render flashes the runner). */
  invulnerable: number;
  alive: boolean;
}

export interface ActivePowerUp {
  kind: PowerUpKind;
  /** Seconds left. */
  remaining: number;
  /** Total seconds. */
  duration: number;
}

export type TutorialHint = 'jump' | 'duck' | 'left' | 'right' | 'switch';

export type RunStatus = 'ready' | 'running' | 'dead';

/**
 * Read-only view of one world, produced once per frame.
 * Arrays may be the world's internal arrays: consumers must not mutate them
 * or hold references across frames.
 */
export interface WorldSnapshot {
  playerId: PlayerId;
  status: RunStatus;
  seed: number;
  /** Seconds of running time. */
  time: number;
  /** Player's track position (m). */
  distance: number;
  /** m/s */
  speed: number;
  /** 0..1 difficulty progress. */
  speedLevel: number;
  score: number;
  coins: number;
  /** Current effective score multiplier. */
  multiplier: number;
  player: PlayerState;
  /** Obstacles with any part inside [distance - BEHIND_DISTANCE, distance + VIEW_DISTANCE]. */
  obstacles: readonly ObstacleState[];
  /** Uncollected items in the same window. */
  collectibles: readonly CollectibleState[];
  powerUps: readonly ActivePowerUp[];
  /** Tutorial prompt to show right now, if any. */
  hint: TutorialHint | null;
}

export type GameEvent =
  | { type: 'jump'; super: boolean }
  | { type: 'land'; onRoof: boolean }
  | { type: 'roll' }
  | { type: 'laneChange'; from: Lane; to: Lane }
  /** Side bump into a train while changing lanes: feedback only, never fatal. */
  | { type: 'blocked'; dir: -1 | 1 }
  /** x, y = item position relative to the runner (m). combo = consecutive coins. */
  | { type: 'coin'; x: number; y: number; combo: number; total: number }
  | { type: 'powerUp'; kind: PowerUpKind }
  /** Fired once, 2 s before a timed power-up runs out. */
  | { type: 'powerUpWarning'; kind: PowerUpKind }
  | { type: 'powerUpEnd'; kind: PowerUpKind }
  /** The hoverboard absorbed a crash. */
  | { type: 'hoverboardBreak' }
  | { type: 'crash'; cause: ObstacleKind; moving: boolean }
  /** Star power-up: the runner smashed through an obstacle (it is removed from the world). */
  | { type: 'smash'; kind: ObstacleKind; lane: Lane }
  /** A moving train is approaching in this lane. */
  | { type: 'trainHorn'; lane: Lane }
  | { type: 'speedUp'; level: number }
  /** Every 500 m. */
  | { type: 'milestone'; distance: number };

export interface WorldOptions {
  /** Same seed ⇒ identical course (both 2P racers get the same seed). */
  seed: number;
  /** Scripted onboarding opening (first ~25 s) with hints. */
  tutorial: boolean;
  /** Attract-mode demo run behind the lobby (no tutorial, no moving trains). */
  attract?: boolean;
}

export interface IWorld {
  readonly playerId: PlayerId;
  /** Fresh run in status 'ready' (runner idles at the start line). */
  reset(opts: WorldOptions): void;
  /** 'ready' → 'running' (call at GO). */
  start(): void;
  handleAction(a: PlayerAction): void;
  /** Advance by a fixed timestep (s). Scrolls only while 'running'; animations still tick otherwise. */
  step(dt: number): void;
  snapshot(): WorldSnapshot;
  /** Events since the last call; clears the queue. */
  drainEvents(): GameEvent[];
}

export interface IAutoPilot {
  /** Action to take this frame, or null. Used for attract mode and soak tests. */
  decide(s: WorldSnapshot): PlayerAction | null;
}

// ---------------------------------------------------------------------------
// Rendering (Three.js)
// ---------------------------------------------------------------------------
/**
 * Player's upper-body pose for the runner to mimic (Settings.mimic).
 * Angles are radians in the screen plane, in DISPLAY space (selfie-mirrored), so
 * "left" = the arm on the screen-left side, which is also the runner's screen-left
 * arm when the camera looks at the runner from behind.
 * Upper arm: 0 = hanging straight down, +PI/2 = raised straight out sideways, PI = straight up.
 * Lower arm: elbow bend relative to the upper arm (0 = straight; positive bends upward/inward).
 */
export interface MimicPose {
  leftUpper: number;
  leftLower: number;
  rightUpper: number;
  rightLower: number;
  /** Torso side lean, radians (+ = top of torso tilts toward screen-right). */
  lean: number;
  /** 0..1 blend over the procedural animation (fades out when tracking is lost). */
  weight: number;
}

export interface RenderView {
  snapshot: WorldSnapshot;
  /** When non-null, pose the runner's arms/torso like the player (legs keep the run cycle). */
  mimic?: MimicPose | null;
  /** Events since the previous render call, for one-shot effects (particles, shake). */
  events: readonly GameEvent[];
}

export interface IRenderer {
  init(canvas: HTMLCanvasElement, opts: { quality: Quality }): void;
  /** 1 = full screen; 2 = vertical split (P1 left half, P2 right half). */
  setPlayerCount(n: PlayerCount): void;
  setQuality(q: Quality): void;
  /** Once per animation frame. views[i] belongs to player i. dt = real seconds since last call. */
  render(views: readonly RenderView[], dt: number): void;
  /** CSS pixels; the renderer applies devicePixelRatio itself. */
  resize(width: number, height: number): void;
  getStats(): { drawCalls: number; triangles: number };
  dispose(): void;
}

// ---------------------------------------------------------------------------
// Audio (procedural Web Audio — no audio files)
// ---------------------------------------------------------------------------
export type UISound =
  | 'click'
  | 'tick'
  | 'go'
  | 'handsUp'
  | 'join'
  | 'pause'
  | 'resume'
  | 'gameOver'
  | 'newRecord'
  | 'win'
  | 'speedUp';

export type MusicMode = 'off' | 'lobby' | 'game' | 'gameover';

export interface IAudio {
  /** Must be called from a user gesture (boot click / first key). Idempotent. */
  unlock(): Promise<void>;
  readonly unlocked: boolean;
  setMuted(m: boolean): void;
  setVolumes(music: number, sfx: number): void;
  /** In 2P, sounds are panned toward the player's half of the screen. */
  playGameEvent(e: GameEvent, playerId: PlayerId, playerCount: PlayerCount): void;
  playUi(s: UISound): void;
  setMusic(mode: MusicMode): void;
  /** 0..1 difficulty; music may speed up / brighten. */
  setIntensity(level: number): void;
  /** 0..1 progress of a hands-up hold (rising charge tone); 0 = silent. */
  setChargeLevel(level: number): void;
  /** Drop all power-up audio (jetpack rumble, slowmo/star music feel), e.g. on restart. */
  clearPowerUps(): void;
  /** Pause holds looping run sounds (jetpack rumble) silent until unpaused. */
  setPaused(paused: boolean): void;
  dispose(): void;
}

// ---------------------------------------------------------------------------
// Keyboard
// ---------------------------------------------------------------------------
export type MenuKey =
  | 'confirm' // Enter, Space
  | 'one-player' // 1
  | 'two-player' // 2
  | 'pause' // P, Escape
  | 'back' // Backspace, Q
  | 'mute' // M
  | 'fullscreen' // F
  | 'settings' // ` (Backquote)
  | 'debug'; // I

export interface IKeyboardInput {
  attach(target: Window): void;
  /** 1P: arrows + WASD + Space/Shift drive player 0. 2P: WASD → player 0, arrows → player 1. */
  setPlayerCount(n: PlayerCount): void;
  onAction(cb: (e: ActionEvent) => void): () => void;
  onMenuKey(cb: (k: MenuKey) => void): () => void;
  dispose(): void;
}

// ---------------------------------------------------------------------------
// Persistence
// ---------------------------------------------------------------------------
export interface LeaderboardEntry {
  id: string;
  name: string;
  score: number;
  coins: number;
  distance: number;
  /** epoch ms */
  date: number;
  /** JPEG data URL (crash selfie) or null. */
  photo: string | null;
  mode: PlayerCount;
}

export interface Settings {
  cameraId: string | null;
  model: PoseModel;
  quality: Quality;
  muted: boolean;
  musicVolume: number;
  sfxVolume: number;
  tutorial: boolean;
  showWebcam: boolean;
  /** Runner's arms/upper body copy the player's pose (camera mode). */
  mimic: boolean;
  vision: VisionTuning;
}

// ---------------------------------------------------------------------------
// UI (DOM overlay) — the App builds a UIModel every frame; the UI renders it.
// ---------------------------------------------------------------------------
export type Screen = 'boot' | 'lobby' | 'countdown' | 'playing' | 'paused' | 'gameover';

export interface PlayerResult {
  playerId: PlayerId;
  name: string;
  score: number;
  coins: number;
  distance: number;
  /** Seconds survived. */
  time: number;
  photo: string | null;
  /** 1-based leaderboard rank if it made the board, else null. */
  rank: number | null;
  /** Leaderboard entry id (for renaming), or null. */
  entryId: string | null;
}

export interface DebugStats {
  fps: number;
  frameMs: number;
  visionFps: number;
  inferenceMs: number;
  drawCalls: number;
}

export interface UIModel {
  screen: Screen;
  playerCount: PlayerCount;
  inputMode: 'camera' | 'keyboard';
  boot: {
    /** 0..1 (model download / init). */
    progress: number;
    message: string;
    error: string | null;
    /** Show the big "tap to start" button (needed to unlock audio + camera). */
    needsClick: boolean;
  };
  lobby: {
    /** One entry per detected person, slot order. */
    people: { slot: PlayerId; handsUpProgress: number; joined: boolean; hints: PositioningHint[] }[];
    /** Seconds left for player 2 to raise hands and join, or null. */
    joinCountdown: number | null;
    spectators: number;
  };
  countdown: {
    /** 3, 2, 1, then 0 = "GO!". */
    value: number;
    /** Per active player, 0..1. */
    calibration: number[];
    message: string | null;
  };
  paused: {
    reason: 'user' | 'tracking-lost';
    /** Seconds until auto-resume, or null while waiting. */
    resumeIn: number | null;
    /** Players currently not visible to the camera. */
    missing: PlayerId[];
  } | null;
  /** Per active player (countdown / playing / paused / gameover). */
  hud: WorldSnapshot[];
  /** 2P: players who already crashed (their half shows "OUT"). */
  out: PlayerId[];
  /** 2P: winner (last runner standing), once decided. */
  winner: PlayerId | null;
  gameOver: {
    results: PlayerResult[];
    /** Seconds until hands-up restart is accepted (lockout), or null when ready. */
    restartLockout: number | null;
    /** 0..1 hands-up progress toward restarting. */
    handsUpProgress: number;
  } | null;
  /** Top entries, best first. */
  leaderboard: LeaderboardEntry[];
  vision: {
    status: VisionStatus;
    frame: VisionFrame | null;
    video: HTMLVideoElement | null;
  };
  settings: Settings;
  settingsOpen: boolean;
  cameras: { deviceId: string; label: string }[];
  /** Non-null when the debug overlay is on. */
  debug: DebugStats | null;
  /** Transient banner text ("SPEED UP!", "P2 WINS — keep going!"); t = performance.now() when raised. */
  toast: { text: string; t: number } | null;
}

export type UICommand =
  /** User gesture on the boot screen: unlock audio + start camera. */
  | { type: 'boot-click' }
  | { type: 'keyboard-only' }
  | { type: 'start'; players: PlayerCount }
  | { type: 'pause-toggle' }
  | { type: 'restart' }
  | { type: 'to-lobby' }
  | { type: 'rename'; entryId: string; name: string }
  | { type: 'settings-open'; open: boolean }
  /** Partial merge; when `vision` is present it replaces the whole VisionTuning. */
  | { type: 'settings-change'; settings: Partial<Settings> }
  | { type: 'clear-leaderboard' }
  | { type: 'fullscreen' };

export interface IUI {
  init(root: HTMLElement): void;
  /** Every animation frame. Must be cheap: diff against the previous model and touch the DOM only on change. */
  render(model: UIModel, dt: number): void;
  onCommand(cb: (c: UICommand) => void): () => void;
  dispose(): void;
}

// ---------------------------------------------------------------------------
// App / URL params
// ---------------------------------------------------------------------------
export interface AppParams {
  /** ?input=keyboard skips the camera entirely. */
  input: 'camera' | 'keyboard';
  /** ?players=1|2 forces the player count (testing). */
  players: PlayerCount | null;
  /** ?seed=123 fixes the course. */
  seed: number | null;
  /** ?debug=1 shows the stats overlay. */
  debug: boolean;
  /** ?autostart=1 skips boot click + lobby and goes straight to countdown (testing). */
  autostart: boolean;
  /** ?autopilot=1 lets the AI drive every player (testing / soak). */
  autopilot: boolean;
  /** ?model=lite|full overrides the saved setting. */
  model: PoseModel | null;
  /** ?quality=low|high overrides the saved setting. */
  quality: Quality | null;
  /** ?mute=1 */
  mute: boolean;
}

// ---------------------------------------------------------------------------
// Booth display (hall.html, a second screen on the same machine)
// The App posts these on BroadcastChannel(BROADCAST_CHANNEL); hall.html listens.
// The leaderboard itself is read from localStorage[STORAGE_KEYS.leaderboard],
// stored as a plain JSON array of LeaderboardEntry, best first.
// ---------------------------------------------------------------------------
export type BoothMessage =
  /** ~4×/s while a run is on screen (countdown / playing / paused). */
  | {
      type: 'state';
      screen: Screen;
      playerCount: PlayerCount;
      players: { score: number; coins: number; distance: number; alive: boolean }[];
    }
  /** Once per finished run, when the gameover screen appears. */
  | { type: 'result'; results: PlayerResult[] }
  /** Lobby / boot: nobody is playing. */
  | { type: 'idle' };
