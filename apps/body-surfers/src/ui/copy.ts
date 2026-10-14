// All player-facing copy + number formatting for the UI.
import type { PositioningHint, PowerUpKind, TutorialHint, VisionStatus } from '../shared/types';

export const fmtScore = (n: number): string => String(Math.max(0, Math.floor(n)));
export const fmtGrouped = (n: number): string => Math.max(0, Math.floor(n)).toLocaleString('en-US');
export const fmtDistance = (m: number): string => `${fmtGrouped(m)} m`;
export const fmtTime = (s: number): string => {
  const t = Math.max(0, Math.floor(s));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
};

/** Lobby chip copy for positioning hints. `left`/`right` are screen directions. */
export const HINT_COPY: Record<PositioningHint, { title: string; sub: string }> = {
  'too-close': { title: 'Step back a bit', sub: "You're too close to the camera" },
  'too-far': { title: 'Come a bit closer', sub: 'We can barely see you' },
  'move-left': { title: 'Scoot left a little', sub: 'Make room for the side lanes' },
  'move-right': { title: 'Scoot right a little', sub: 'Make room for the side lanes' },
  'head-cut-off': { title: 'Keep your head in view', sub: 'Step back or tilt the screen back' },
  'low-visibility': { title: 'Find some light', sub: 'Face a lamp or a window' },
};

export interface TutorialCopy {
  label: string;
  /** Keyboard keys to show under the banner, per keyboard layout. */
  keys1p: string[];
  keysP1: string[];
  keysP2: string[];
}

export const TUTORIAL_COPY: Record<TutorialHint, TutorialCopy> = {
  jump: { label: 'JUMP!', keys1p: ['↑', 'W', 'SPACE'], keysP1: ['W', 'SPACE'], keysP2: ['↑'] },
  duck: { label: 'SQUAT!', keys1p: ['↓', 'S', 'SHIFT'], keysP1: ['S', 'L-SHIFT'], keysP2: ['↓', 'R-SHIFT'] },
  left: { label: 'STEP LEFT', keys1p: ['←', 'A'], keysP1: ['A'], keysP2: ['←'] },
  right: { label: 'STEP RIGHT', keys1p: ['→', 'D'], keysP1: ['D'], keysP2: ['→'] },
  switch: { label: 'SWITCH LANE', keys1p: ['←', '→'], keysP1: ['A', 'D'], keysP2: ['←', '→'] },
};

export const POWERUP_LABEL: Record<PowerUpKind, string> = {
  magnet: 'MAGNET',
  multiplier: '2X SCORE',
  sneakers: 'SUPER SNEAKERS',
  hoverboard: 'HOVERBOARD',
  jetpack: 'JETPACK',
  slowmo: 'SLOW-MO',
  star: 'STAR POWER',
  coinRain: 'COIN RAIN',
};

/** Short callout shown in the top callout stack when a power-up starts. */
export const POWERUP_TOAST: Record<PowerUpKind, string> = {
  magnet: 'MAGNET!',
  multiplier: '2X SCORE!',
  sneakers: 'SUPER SNEAKERS!',
  hoverboard: 'HOVERBOARD!',
  jetpack: 'JETPACK!',
  slowmo: 'SLOW-MO!',
  star: 'STAR POWER!',
  coinRain: 'COIN RAIN!',
};

/** Kinds that get the top callout pop (the originals already have world/toast feedback). */
export const POWERUP_POP: ReadonlySet<PowerUpKind> = new Set<PowerUpKind>(['jetpack', 'slowmo', 'star', 'coinRain']);

export const POWERUP_ORDER: readonly PowerUpKind[] = [
  'star',
  'jetpack',
  'hoverboard',
  'slowmo',
  'magnet',
  'multiplier',
  'sneakers',
  'coinRain',
];

export const CAMERA_STATUS_COPY: Record<VisionStatus, string> = {
  idle: 'CAMERA OFF',
  'loading-model': 'LOADING…',
  'model-ready': 'CAMERA OFF',
  'starting-camera': 'STARTING CAMERA…',
  running: 'WAITING FOR VIDEO…',
  error: 'CAMERA UNAVAILABLE',
};

export type CameraErrorKind = 'blocked' | 'missing' | 'busy' | 'disconnected' | 'model' | 'unknown';

/**
 * Classify a raw getUserMedia / MediaPipe error string. Pure (no DOM) so it is unit-tested.
 * Order matters: permission wins over everything (the blocked message says "reload", which
 * must never be read as a model "load" failure), and "load" only matches as a whole word.
 */
export function classifyCameraError(raw: string): CameraErrorKind {
  const e = raw.toLowerCase();
  if (/notallowed|securityerror|permission|blocked|denied/.test(e)) return 'blocked';
  if (/disconnect|unplugged|track ended|ended unexpectedly/.test(e)) return 'disconnected';
  if (/notfound|not found|no camera|devicesnotfound|overconstrained/.test(e)) return 'missing';
  if (/notreadable|in use|could ?n[o']t start|trackstart|busy|aborterror/.test(e)) return 'busy';
  if (/model|wasm|fetch|network|\bload(ed|ing)?\b|\bto load\b/.test(e)) return 'model';
  return 'unknown';
}

export interface FriendlyError {
  kind: CameraErrorKind;
  title: string;
  body: string;
  /** Only the steps that fit this error (never repeats the body). */
  tips: readonly string[];
}

const ERROR_COPY: Record<CameraErrorKind, Omit<FriendlyError, 'kind'>> = {
  blocked: {
    title: 'CAMERA BLOCKED',
    body: "The browser isn't letting the game use your camera.",
    tips: ['Click the camera icon in the address bar', 'Choose "Allow", then press TRY AGAIN'],
  },
  disconnected: {
    title: 'CAMERA UNPLUGGED',
    body: 'The camera disconnected.',
    tips: ['Plug it back in, then press TRY AGAIN', 'Or keep going with the keyboard'],
  },
  missing: {
    title: 'NO CAMERA FOUND',
    body: "We couldn't find a webcam.",
    tips: ['Plug in a webcam, then press TRY AGAIN', 'Or pick another camera in Settings'],
  },
  busy: {
    title: 'CAMERA IS BUSY',
    body: 'Another app is using the camera.',
    tips: ['Quit Zoom, FaceTime, Meet or Photo Booth', 'Close other tabs that use the camera'],
  },
  model: {
    title: "BODY TRACKING DIDN'T LOAD",
    body: 'The pose model failed to download.',
    tips: ['Check your connection and reload the page', 'Still stuck? Play with the keyboard'],
  },
  unknown: {
    title: 'CAMERA TROUBLE',
    body: "The camera didn't start.",
    tips: ['Check the camera, then press TRY AGAIN', 'Or play with the keyboard'],
  },
};

/** Turn a raw getUserMedia / MediaPipe error into something a passer-by can act on. */
export function friendlyError(raw: string): FriendlyError {
  const kind = classifyCameraError(raw);
  return { kind, ...ERROR_COPY[kind] };
}

/** One-line controls primer (boot + lobby). */
export const CONTROLS_CAMERA: readonly { icon: string; label: string }[] = [
  { icon: '⬅➡', label: 'Step' },
  { icon: '⬆', label: 'Jump' },
  { icon: '⬇', label: 'Squat' },
];
export const CONTROLS_KEYBOARD: readonly { icon: string; label: string }[] = [
  { icon: '←→', label: 'Arrows' },
  { icon: '␣', label: 'Space' },
];
