// Audio tunables (Audio+Input agent). Gains are linear, times are seconds.
import type { MusicMode } from '../shared/types';

// ---- Mix ---------------------------------------------------------------------
/** Bus gain = volume ^ VOLUME_CURVE (perceptual taper for the 0..1 settings sliders). */
export const VOLUME_CURVE = 2;
export const DEFAULT_MUSIC_VOLUME = 0.6;
export const DEFAULT_SFX_VOLUME = 0.8;
/** Gentle glue + safety limiting on the whole mix. */
export const COMPRESSOR = { threshold: -14, knee: 12, ratio: 4, attack: 0.004, release: 0.22 } as const;
/** Mute / volume change smoothing (setTargetAtTime time constants). */
export const MUTE_TC = 0.02;
export const VOLUME_TC = 0.05;
/** 2P: P1 sounds pan left, P2 right. */
export const PAN_2P = 0.55;
/** One-shots start this far after currentTime so their attack is never already in the past. */
export const SFX_LEAD = 0.012;
/** Procedural room reverb (send effect). */
export const REVERB = { seconds: 1.5, decay: 3.2, sfxReturn: 0.55, musicReturn: 0.5 } as const;

// ---- Voices ------------------------------------------------------------------
export type VoiceCategory =
  | 'coin'
  | 'move'
  | 'jump'
  | 'land'
  | 'roll'
  | 'blocked'
  | 'crash'
  | 'powerUp'
  | 'warning'
  | 'horn'
  | 'speedUp'
  | 'milestone'
  | 'smash'
  | 'ui'
  | 'misc';

/** Max simultaneous voices per category (the oldest is faded out when exceeded). */
export const VOICE_CAPS: Readonly<Record<VoiceCategory, number>> = {
  coin: 6,
  move: 3,
  jump: 3,
  land: 3,
  roll: 2,
  blocked: 2,
  crash: 2,
  powerUp: 2,
  warning: 2,
  horn: 2,
  speedUp: 1,
  milestone: 2,
  smash: 3,
  ui: 6,
  misc: 4,
};
export const MAX_SFX_VOICES = 32;
/** Fade applied to a stolen voice. */
export const STEAL_TC = 0.008;

// ---- Coins (the signature sound) ---------------------------------------------
export const COIN = {
  /** E5: first coin of a combo. */
  baseMidi: 76,
  /** C-major pentatonic, expressed as semitones above E (E G A C D). Always consonant with the music. */
  scale: [0, 3, 5, 8, 10] as readonly number[],
  /** Combo climbs this many scale steps (E5 → C7), then cycles through the top octave. */
  climbSteps: 8,
  cycleFrom: 4,
  /** Coins collected in the same instant are "strummed" this far apart. */
  minInterval: 0.034,
  /** Drop a coin blip rather than queue it later than this. */
  maxQueue: 0.2,
  /** Combo at which an extra sparkle layer joins. */
  hotStreak: 10,
} as const;

/** Per-event throttles / cross-player de-duplication windows. */
export const DEDUPE = { speedUp: 0.6, milestone: 0.3, laneChange: 0.05, warning: 0.4, crashDuck: 0.2, smash: 0.07 } as const;

// ---- Music scheduler ---------------------------------------------------------
export const SCHEDULER = {
  intervalMs: 25,
  /** Schedule notes this far ahead; grows when the main thread stalls. */
  minLookahead: 0.12,
  maxLookahead: 0.4,
  /** Notes due sooner than this are skipped — never scheduled late. */
  minLead: 0.015,
  /** First note after a (re)start. */
  startDelay: 0.06,
  /** Behind by more than this → resync to the next beat instead of skipping step by step. */
  maxCatchUp: 1.0,
} as const;

export type ActiveMusicMode = Exclude<MusicMode, 'off'>;
export const MUSIC_BPM: Readonly<Record<ActiveMusicMode, number>> = { lobby: 112, game: 124, gameover: 88 };
export const MUSIC_SWING: Readonly<Record<ActiveMusicMode, number>> = { lobby: 0.12, game: 0, gameover: 0.06 };
/** setIntensity(1) speeds the game music up by this fraction. */
export const INTENSITY_TEMPO_BOOST = 0.12;
/** Music on/off fade time constant. */
export const MUSIC_FADE_TC = 0.18;
/** Music bus level per mode (the mix is calibrated with the offline analyzer in the sandbox). */
export const MUSIC_LEVEL: Readonly<Record<ActiveMusicMode, number>> = { lobby: 0.9, game: 1, gameover: 0.85 };

// ---- Charge tone -------------------------------------------------------------
export const CHARGE = { lowHz: 196, octaves: 2, tc: 0.03, teardownAfter: 0.6, peak: 0.12 } as const;

/** Unlock: stop waiting for AudioContext.resume() after this (it can hang without a gesture). */
export const UNLOCK_TIMEOUT_MS = 600;

// ---- Power-up music colouring / loops ----------------------------------------
/** Music feel while a power-up is active: tempo rate, pitch ratio, filter cutoff scale. */
export const SLOWMO_FEEL = { rate: 0.82, pitch: 0.94, tone: 0.12 } as const;
export const STAR_FEEL = { rate: 1.08, pitch: 1, tone: 1.6 } as const;
/** Jetpack thrust rumble (looped low noise) level. */
export const JETPACK_RUMBLE = { peak: 0.07, lowHz: 160, tc: 0.08 } as const;
