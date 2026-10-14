// ============================================================================
// FROZEN SHARED CONSTANTS — world dimensions that gameplay (collision) and
// rendering (meshes) must agree on, plus app-wide defaults.
// Owned by the orchestrator; agents must not edit. Module-specific tunables
// belong in each module's own config.ts.
//
// Coordinate system (render space): x = right, y = up, -z = forward.
// The runner stays at z = 0; an object at track position s is drawn at
// z = -(s - snapshot.distance). Lane centers sit at x = lane * LANE_WIDTH.
// ============================================================================
import type { Lane, ObstacleKind, Settings, VisionTuning } from './types';

export const GAME_TITLE = 'BODY SURFERS';

// ---- Track ----------------------------------------------------------------
export const LANE_WIDTH = 2.6;
export const LANES: readonly Lane[] = [-1, 0, 1];
export const laneX = (lane: Lane): number => lane * LANE_WIDTH;

/** Meters ahead the world keeps populated and the renderer draws. */
export const VIEW_DISTANCE = 170;
/** Meters behind the runner that objects stay alive/visible. */
export const BEHIND_DISTANCE = 20;

// ---- Runner (collision box; feet at y) -------------------------------------
export const PLAYER_WIDTH = 0.8;
export const PLAYER_DEPTH = 0.6;
export const PLAYER_HEIGHT = 1.75;
export const PLAYER_ROLL_HEIGHT = 0.8;

// ---- Obstacles ---------------------------------------------------------------
export const TRAIN_WIDTH = 2.3;
export const TRAIN_HEIGHT = 3.2;
/** A train obstacle is 1–3 cars: length = cars * TRAIN_CAR_LENGTH. */
export const TRAIN_CAR_LENGTH = 13;

/** Hurdle you must JUMP over. */
export const BARRIER_LOW_HEIGHT = 1.0;
export const BARRIER_LOW_DEPTH = 0.4;

/** Overhead bar you must ROLL under: open gap from the ground to CLEARANCE. */
export const BARRIER_HIGH_CLEARANCE = 1.15;
export const BARRIER_HIGH_TOP = 2.7;
export const BARRIER_HIGH_DEPTH = 0.4;

export const BARRIER_WIDTH = 2.2;

/** Ramp rises linearly from 0 to TRAIN_HEIGHT over RAMP_LENGTH; always directly in front of a train in the same lane. */
export const RAMP_LENGTH = 7;
export const RAMP_WIDTH = 2.2;

/** Top height of an obstacle kind (ramp: height at its far end). */
export function obstacleTop(kind: ObstacleKind): number {
  switch (kind) {
    case 'barrier_low':
      return BARRIER_LOW_HEIGHT;
    case 'barrier_high':
      return BARRIER_HIGH_TOP;
    case 'train':
    case 'ramp':
      return TRAIN_HEIGHT;
  }
}

/** Width (x extent) of an obstacle kind. */
export function obstacleWidth(kind: ObstacleKind): number {
  switch (kind) {
    case 'train':
      return TRAIN_WIDTH;
    case 'ramp':
      return RAMP_WIDTH;
    default:
      return BARRIER_WIDTH;
  }
}

// ---- Collectibles -----------------------------------------------------------
export const COIN_RADIUS = 0.42;
/** Default coin center height above ground/roof. */
export const COIN_HEIGHT = 1.0;

// ---- Simulation ---------------------------------------------------------------
export const SIM_HZ = 120;
export const SIM_DT = 1 / SIM_HZ;

// ---- People / players ---------------------------------------------------------
/** Hold both hands up this long to start / join / restart. */
export const HANDS_UP_HOLD_MS = 900;
/** P1 orange, P2 blue — runner hoodie, overlay skeleton, HUD accents. */
export const PLAYER_COLORS = ['#ff7a1a', '#1aa3ff'] as const;
export const PLAYER_LABELS = ['P1', 'P2'] as const;

// ---- Persistence ----------------------------------------------------------------
export const MAX_LEADERBOARD = 10;
export const STORAGE_KEYS = {
  settings: 'bodysurfers.settings.v1',
  leaderboard: 'bodysurfers.leaderboard.v1',
} as const;

export const DEFAULT_VISION_TUNING: VisionTuning = {
  laneThreshold: 0.8,
  laneHysteresis: 0.3,
  jumpThreshold: 0.3,
  duckThreshold: 0.5,
  smoothing: 0.5,
  mirror: true,
};

export const DEFAULT_SETTINGS: Settings = {
  cameraId: null,
  model: 'lite',
  quality: 'high',
  muted: false,
  musicVolume: 0.6,
  sfxVolume: 0.8,
  tutorial: true,
  showWebcam: true,
  mimic: true,
  vision: { ...DEFAULT_VISION_TUNING },
};

// ---- Asset paths (relative to Vite BASE_URL; everything is self-hosted) -------
export const ASSET_PATHS = {
  mediapipeWasm: 'mediapipe/wasm',
  poseModel: (model: 'lite' | 'full') => `models/pose_landmarker_${model}.task`,
} as const;

/** BroadcastChannel name for the live booth feed (see BoothMessage). */
export const BROADCAST_CHANNEL = 'bodysurfers-booth';
