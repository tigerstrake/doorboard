// Low-level synthesis helpers: pitch math, procedural buffers, click-free envelopes.
import { Rng } from '../shared/rng';
import { COIN } from './config';

export const midiToHz = (midi: number): number => 440 * Math.pow(2, (midi - 69) / 12);
export const centsToRatio = (cents: number): number => Math.pow(2, cents / 1200);
export const clamp01 = (v: number): number => (v > 0 ? (v < 1 ? v : 1) : 0);
/** NaN/±Infinity-safe 0..1 input sanitizer (falls back to `fallback`). */
export const unit = (v: number, fallback: number): number => (Number.isFinite(v) ? clamp01(v) : fallback);

/** Exponential envelopes are considered silent after this many time constants (e^-8 ≈ -70 dB). */
export const TC_TAIL = 8;

// ---- Coin pitch (pure; unit-tested) ------------------------------------------

/** Semitones above COIN.baseMidi for the n-th coin of a combo (combo ≥ 1). */
export function coinSemitones(combo: number): number {
  const n = Math.max(0, Math.floor(Number.isFinite(combo) ? combo : 1) - 1);
  const len = COIN.scale.length;
  const idx = n <= COIN.climbSteps ? n : COIN.cycleFrom + ((n - COIN.cycleFrom) % (COIN.climbSteps - COIN.cycleFrom + 1));
  return Math.floor(idx / len) * 12 + COIN.scale[idx % len];
}

export function coinFrequency(combo: number): number {
  return midiToHz(COIN.baseMidi + coinSemitones(combo));
}

/** Equal-loudness-ish compensation: higher blips play a little softer. */
export function coinGain(freq: number): number {
  return Math.pow(midiToHz(COIN.baseMidi) / freq, 0.35);
}

// ---- Procedural buffers ------------------------------------------------------

/** Deterministic white (or pink) noise, meant to be played looped from a random offset. */
export function createNoiseBuffer(ctx: BaseAudioContext, seconds: number, seed: number, pink = false): AudioBuffer {
  const len = Math.max(1, Math.floor(ctx.sampleRate * seconds));
  const buf = ctx.createBuffer(1, len, ctx.sampleRate);
  const d = buf.getChannelData(0);
  const rng = new Rng(seed);
  if (!pink) {
    for (let i = 0; i < len; i++) d[i] = rng.next() * 2 - 1;
    return buf;
  }
  // Paul Kellet's refined pink filter.
  let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
  for (let i = 0; i < len; i++) {
    const w = rng.next() * 2 - 1;
    b0 = 0.99886 * b0 + w * 0.0555179;
    b1 = 0.99332 * b1 + w * 0.0750759;
    b2 = 0.969 * b2 + w * 0.153852;
    b3 = 0.8665 * b3 + w * 0.3104856;
    b4 = 0.55 * b4 + w * 0.5329522;
    b5 = -0.7616 * b5 - w * 0.016898;
    d[i] = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362) * 0.11;
    b6 = w * 0.115926;
  }
  return buf;
}

/** Stereo room impulse response: decorrelated noise with a darkening, decaying tail. */
export function createImpulseResponse(ctx: BaseAudioContext, seconds: number, decay: number, seed: number): AudioBuffer {
  const sr = ctx.sampleRate;
  const len = Math.max(2, Math.floor(sr * seconds));
  const buf = ctx.createBuffer(2, len, sr);
  const rng = new Rng(seed);
  const fadeIn = Math.max(1, Math.floor(sr * 0.004));
  for (let ch = 0; ch < 2; ch++) {
    const d = buf.getChannelData(ch);
    let lp = 0;
    for (let i = 0; i < len; i++) {
      const x = i / len;
      const a = 0.15 + 0.75 * x; // high frequencies die first
      lp = lp * a + (rng.next() * 2 - 1) * (1 - a);
      const env = Math.pow(1 - x, decay) * (i < fadeIn ? i / fadeIn : 1);
      d[i] = lp * env * (1 + 2 * x); // compensate the low-pass level drop
    }
  }
  return buf;
}

/** tanh soft-clip curve (adds harmonics so bass is audible on laptop speakers). */
export function createSoftClipCurve(drive: number, n = 1024): Float32Array<ArrayBuffer> {
  const curve = new Float32Array(n);
  const norm = Math.tanh(drive);
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * 2 - 1;
    curve[i] = Math.tanh(x * drive) / norm;
  }
  return curve;
}

// ---- Envelopes (all click-free: linear attack from 0, exponential release) ----

/** Linear attack to `peak`, then exponential decay (time constant `tc`). Returns when it is ≈ -70 dB. */
export function perc(p: AudioParam, t: number, peak: number, attack: number, tc: number): number {
  p.setValueAtTime(0, t);
  p.linearRampToValueAtTime(peak, t + attack);
  p.setTargetAtTime(0, t + attack, tc);
  return t + attack + tc * TC_TAIL;
}

/** Attack, hold at `peak`, exponential release. Returns when it is ≈ -70 dB. */
export function ahr(p: AudioParam, t: number, peak: number, attack: number, hold: number, tc: number): number {
  p.setValueAtTime(0, t);
  p.linearRampToValueAtTime(peak, t + attack);
  if (hold > 0) p.setValueAtTime(peak, t + attack + hold);
  p.setTargetAtTime(0, t + attack + hold, tc);
  return t + attack + hold + tc * TC_TAIL;
}

/** Attack, decay to a sustain level, hold, release. Returns when it is ≈ -70 dB. */
export function adsr(p: AudioParam, t: number, peak: number, attack: number, decayTc: number, sustain: number, holdUntil: number, releaseTc: number): number {
  p.setValueAtTime(0, t);
  p.linearRampToValueAtTime(peak, t + attack);
  p.setTargetAtTime(peak * sustain, t + attack, decayTc);
  const rel = Math.max(holdUntil, t + attack);
  p.setTargetAtTime(0, rel, releaseTc);
  return rel + releaseTc * TC_TAIL;
}

/** Exponential glide between two positive values. */
export function glide(p: AudioParam, t: number, from: number, to: number, dur: number): void {
  p.setValueAtTime(Math.max(from, 1e-4), t);
  p.exponentialRampToValueAtTime(Math.max(to, 1e-4), t + Math.max(dur, 1e-3));
}
