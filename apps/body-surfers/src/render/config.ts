// Render-module tunables (art direction, camera, quality, environment layout).
import { VIEW_DISTANCE } from '../shared/constants';
import type { Quality } from '../shared/types';

/** Decoration segments: content is a pure function of (seed, segment index) so recycling never pops. */
export const SEGMENT_LENGTH = 40;
/** Repeat length of the periodic layer (ground texture, sleepers, gantries). */
export const PERIOD = 40;
/** Meters of environment kept behind / ahead of the runner. */
export const ENV_BEHIND = 40;
export const ENV_AHEAD = VIEW_DISTANCE + 40;

/** Lateral layout (meters, absolute |x|). */
export const LAYOUT = {
  railGauge: 0.75, // rail x offset from lane center
  trackEdge: 3.9, // outer edge of the ballast bed
  poleX: 4.55,
  wallInnerX: 5.25,
  wallThickness: 0.5,
  wallHeight: 3.3,
  wallPanel: 10,
  treeX: 6.9,
  buildingX: 8.6,
  backRowX: 24,
} as const;

export const COLORS = {
  skyTop: '#46b6f5',
  skyHorizon: '#dff4ff',
  fog: '#d9f1ff',
  sun: '#fff2d9',
  hemiSky: '#d6efff',
  hemiGround: '#b29a7c',
  sleeper: '#7a5233',
  railTop: '#eef2f6',
  railSide: '#77808c',
  wire: '#2d333b',
  pole: '#8d98a6',
  poleDark: '#5c6672',
  outerGround: '#b9ae9f',
  skin: '#ffcc9c',
  skinShade: '#f0b285',
  hair: '#4b2e1f',
  jeans: '#3f6fd0',
  jeansDark: '#2c4f9c',
  shoe: '#fbfbf7',
  /** Cap colors per player (complement the hoodie). */
  cap: ['#22306a', '#e8394a'] as const,
  coin: '#ffc62e',
  coinRim: '#e09a12',
  coinStar: '#fff09a',
} as const;

/** Train liveries indexed by ObstacleState.variant % 5: [body, accent, trim]. */
export const LIVERIES: readonly (readonly [string, string, string])[] = [
  ['#e8323e', '#ffd23f', '#ffffff'], // red / yellow
  ['#1f6fe0', '#f4f7fb', '#9fd0ff'], // blue / white
  ['#22b26b', '#f4f7fb', '#b8f2cf'], // green / white
  ['#ffc928', '#1d4ed8', '#ffffff'], // yellow / blue
  ['#7b3fe4', '#ffd23f', '#e2d1ff'], // purple / yellow
];

export const PASTELS: readonly string[] = [
  '#ffb3a7', '#ffd6a5', '#fdf0a6', '#caffbf', '#9bf6ff', '#a0c4ff', '#bdb2ff', '#ffc6ff',
  '#f7c59f', '#b8e0d2', '#f4a6b7', '#c3e8ff', '#ffe0b3', '#d7c4f2',
];

export const CAMERA = {
  posY: 3.4,
  posZ: 7.2,
  lookY: 1.4,
  lookZ: -10,
  followX: 0.85,
  followXNarrow: 0.62,
  followY: 0.6,
  fovMin: 62,
  fovMax: 72,
  /** Below this aspect, vertical FOV is derived from the horizontal FOV at this aspect. */
  refAspect: 1.25,
  maxFov: 92,
  near: 0.1,
  far: 700,
  dampX: 7,
  dampY: 4.5,
  dampLookY: 5,
} as const;

export interface QualityProfile {
  maxPixelRatio: number;
  shadows: boolean;
  shadowMapSize: number;
  speedLines: boolean;
  particleScale: number;
}

export const QUALITY: Record<Quality, QualityProfile> = {
  high: { maxPixelRatio: 2, shadows: true, shadowMapSize: 2048, speedLines: true, particleScale: 1 },
  low: { maxPixelRatio: 1, shadows: false, shadowMapSize: 1024, speedLines: false, particleScale: 0.5 },
};

/** Power-up glow colors. */
export const POWERUP_COLORS = {
  magnet: '#ff4d5a',
  multiplier: '#b45cff',
  sneakers: '#3dff8f',
  hoverboard: '#28e0ff',
  jetpack: '#ff8a1f',
  slowmo: '#5ab4ff',
  star: '#ffd23f',
  coinRain: '#ffe14d',
} as const;
