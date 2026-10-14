// Settings: validation (corrupt / partial / out-of-range data tolerated), merging
// UI patches, URL overrides and persistence.
import { DEFAULT_SETTINGS, DEFAULT_VISION_TUNING, STORAGE_KEYS } from '../shared/constants';
import { clamp } from '../shared/math';
import type { AppParams, Settings, VisionTuning } from '../shared/types';
import { isRecord, readJson, writeJson, type StorageLike } from './storage';

/** Slider ranges (MASTERPLAN §7); stored values are clamped into them. */
export const TUNING_RANGES = {
  laneThreshold: [0.5, 1.5],
  laneHysteresis: [0.1, 0.6],
  jumpThreshold: [0.15, 0.6],
  duckThreshold: [0.25, 0.8],
  smoothing: [0, 1],
} as const;

const num = (v: unknown, fallback: number, lo: number, hi: number): number =>
  typeof v === 'number' && Number.isFinite(v) ? clamp(v, lo, hi) : fallback;
const bool = (v: unknown, fallback: boolean): boolean => (typeof v === 'boolean' ? v : fallback);

export function sanitizeTuning(raw: unknown, base: VisionTuning = DEFAULT_VISION_TUNING): VisionTuning {
  const r = isRecord(raw) ? raw : {};
  const R = TUNING_RANGES;
  return {
    laneThreshold: num(r.laneThreshold, base.laneThreshold, R.laneThreshold[0], R.laneThreshold[1]),
    laneHysteresis: num(r.laneHysteresis, base.laneHysteresis, R.laneHysteresis[0], R.laneHysteresis[1]),
    jumpThreshold: num(r.jumpThreshold, base.jumpThreshold, R.jumpThreshold[0], R.jumpThreshold[1]),
    duckThreshold: num(r.duckThreshold, base.duckThreshold, R.duckThreshold[0], R.duckThreshold[1]),
    smoothing: num(r.smoothing, base.smoothing, R.smoothing[0], R.smoothing[1]),
    mirror: bool(r.mirror, base.mirror),
  };
}

/** Any value → a complete, valid Settings object (missing/invalid fields come from `base`). */
export function sanitizeSettings(raw: unknown, base: Settings = DEFAULT_SETTINGS): Settings {
  const r = isRecord(raw) ? raw : {};
  let cameraId = base.cameraId;
  if (typeof r.cameraId === 'string') cameraId = r.cameraId === '' ? null : r.cameraId;
  else if (r.cameraId === null) cameraId = null;
  return {
    cameraId,
    model: r.model === 'lite' || r.model === 'full' ? r.model : base.model,
    quality: r.quality === 'low' || r.quality === 'high' ? r.quality : base.quality,
    muted: bool(r.muted, base.muted),
    musicVolume: num(r.musicVolume, base.musicVolume, 0, 1),
    sfxVolume: num(r.sfxVolume, base.sfxVolume, 0, 1),
    tutorial: bool(r.tutorial, base.tutorial),
    showWebcam: bool(r.showWebcam, base.showWebcam),
    mimic: bool(r.mimic, base.mimic),
    vision: sanitizeTuning(r.vision, base.vision),
  };
}

/**
 * Apply a UI `settings-change` patch. Per the UICommand contract a present `vision`
 * replaces the whole tuning (fields it lacks keep their current values).
 */
export function mergeSettings(current: Settings, patch: Partial<Settings>): Settings {
  return sanitizeSettings({ ...current, ...patch, vision: patch.vision ?? current.vision }, current);
}

/** ?model / ?quality / ?mute win over the saved values for this session only (never persisted). */
export function applyParamOverrides(s: Settings, p: Pick<AppParams, 'model' | 'quality' | 'mute'>): Settings {
  if (p.model === null && p.quality === null && !p.mute) return s;
  return { ...s, model: p.model ?? s.model, quality: p.quality ?? s.quality, muted: p.mute ? true : s.muted };
}

export function sameTuning(a: VisionTuning, b: VisionTuning): boolean {
  return (
    a.laneThreshold === b.laneThreshold &&
    a.laneHysteresis === b.laneHysteresis &&
    a.jumpThreshold === b.jumpThreshold &&
    a.duckThreshold === b.duckThreshold &&
    a.smoothing === b.smoothing &&
    a.mirror === b.mirror
  );
}

/** laneThreshold default before it was lowered; stored copies of it are migrated on load. */
export const OLD_DEFAULT_LANE_THRESHOLD = 0.9;

/**
 * Upgrade raw stored settings from older builds (load path only, so a value the player
 * picks in this session is never rewritten). An untouched old-default laneThreshold (0.9)
 * becomes the current default.
 */
export function migrateStoredSettings(raw: unknown): unknown {
  if (!isRecord(raw) || !isRecord(raw.vision)) return raw;
  if (raw.vision.laneThreshold !== OLD_DEFAULT_LANE_THRESHOLD) return raw;
  return { ...raw, vision: { ...raw.vision, laneThreshold: DEFAULT_VISION_TUNING.laneThreshold } };
}

export class SettingsStore {
  constructor(
    private readonly storage: StorageLike,
    private readonly key: string = STORAGE_KEYS.settings,
  ) {}

  load(): Settings {
    return sanitizeSettings(migrateStoredSettings(readJson(this.storage, this.key)));
  }

  save(s: Settings): boolean {
    return writeJson(this.storage, this.key, s);
  }
}
