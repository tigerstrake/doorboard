// Procedural music: a 4-bar loop (C – G – Am – F) on a lookahead scheduler.
// Modes: lobby (chill), game (full 4-on-the-floor), gameover (sparse), off.
import type { MusicMode } from '../shared/types';
import type { ActiveMusicMode } from './config';
import {
  INTENSITY_TEMPO_BOOST,
  MUSIC_BPM,
  MUSIC_FADE_TC,
  MUSIC_LEVEL,
  MUSIC_SWING,
  SCHEDULER,
} from './config';
import { TC_TAIL, ahr, clamp01, createSoftClipCurve, glide, midiToHz, perc } from './dsp';

const STEPS_PER_BAR = 16;
const BARS = 4;
const LOOP_STEPS = STEPS_PER_BAR * BARS;

/** Chord per bar: root MIDI (bass octave) + triad offsets. C, G, Am, F. */
const CHORDS: readonly { root: number; triad: readonly number[] }[] = [
  { root: 36, triad: [0, 4, 7] }, // C
  { root: 43, triad: [0, 4, 7] }, // G
  { root: 45, triad: [0, 3, 7] }, // Am
  { root: 41, triad: [0, 4, 7] }, // F
];

/** Game lead: one entry per 16th step over 4 bars (MIDI, 0 = rest). Catchy pentatonic hook. */
const LEAD_GAME: readonly number[] = [
  // bar 1 (C)
  72, 0, 76, 0, 79, 0, 76, 0, 0, 0, 74, 0, 72, 0, 0, 0,
  // bar 2 (G)
  71, 0, 74, 0, 79, 0, 74, 0, 0, 0, 76, 0, 74, 0, 0, 0,
  // bar 3 (Am)
  72, 0, 76, 0, 81, 0, 79, 0, 0, 0, 76, 0, 79, 0, 81, 0,
  // bar 4 (F)
  84, 0, 0, 0, 81, 0, 79, 0, 77, 0, 76, 0, 74, 0, 0, 0,
];
/** Lobby bells: sparse. */
const LEAD_LOBBY: readonly number[] = [
  79, 0, 0, 0, 0, 0, 76, 0, 0, 0, 0, 0, 0, 0, 0, 0,
  74, 0, 0, 0, 0, 0, 79, 0, 0, 0, 0, 0, 0, 0, 0, 0,
  76, 0, 0, 0, 0, 0, 72, 0, 0, 0, 0, 0, 74, 0, 0, 0,
  72, 0, 0, 0, 0, 0, 69, 0, 0, 0, 0, 0, 0, 0, 0, 0,
];
/** Game over: slow descending line. */
const LEAD_GAMEOVER: readonly number[] = [
  79, 0, 0, 0, 0, 0, 0, 0, 76, 0, 0, 0, 0, 0, 0, 0,
  74, 0, 0, 0, 0, 0, 0, 0, 71, 0, 0, 0, 0, 0, 0, 0,
  72, 0, 0, 0, 0, 0, 0, 0, 69, 0, 0, 0, 0, 0, 0, 0,
  65, 0, 0, 0, 0, 0, 0, 0, 67, 0, 0, 0, 0, 0, 0, 0,
];

export class MusicEngine {
  private readonly modeGain: GainNode;
  private readonly duckGain: GainNode;
  private readonly filter: BiquadFilterNode;
  private readonly drumBus: GainNode;
  private readonly bassBus: GainNode;
  private readonly synthBus: GainNode;
  private mode: MusicMode = 'off';
  private active: ActiveMusicMode = 'lobby';
  private intensity = 0;
  /** Power-up colouring: tempo × rate, pitch × pitch, filter cutoff × tone. */
  private rate = 1;
  private pitch = 1;
  private tone = 1;
  private timer: ReturnType<typeof setInterval> | null = null;
  private stopTimer: ReturnType<typeof setTimeout> | null = null;
  private nextTime = 0;
  private step = 0;
  private lookahead: number = SCHEDULER.minLookahead;
  private lastTick = 0;

  constructor(
    private readonly ctx: AudioContext,
    dest: AudioNode,
    private readonly noise: AudioBuffer,
    private readonly reverbSend: AudioNode | null,
  ) {
    this.modeGain = ctx.createGain();
    this.modeGain.gain.value = 0;
    this.duckGain = ctx.createGain();
    this.filter = ctx.createBiquadFilter();
    this.filter.type = 'lowpass';
    this.filter.frequency.value = 9000;
    this.filter.Q.value = 0.8;
    this.drumBus = ctx.createGain();
    this.drumBus.gain.value = 0.9;
    this.bassBus = ctx.createGain();
    this.bassBus.gain.value = 0.55;
    this.synthBus = ctx.createGain();
    this.synthBus.gain.value = 0.5;
    const shaper = ctx.createWaveShaper();
    shaper.curve = createSoftClipCurve(2.2);
    this.bassBus.connect(shaper);
    shaper.connect(this.filter);
    this.drumBus.connect(this.filter);
    this.synthBus.connect(this.filter);
    this.filter.connect(this.duckGain);
    this.duckGain.connect(this.modeGain);
    this.modeGain.connect(dest);
  }

  get currentMode(): MusicMode {
    return this.mode;
  }

  setMode(mode: MusicMode): void {
    if (mode === this.mode) return;
    const now = this.ctx.currentTime;
    this.mode = mode;
    if (this.stopTimer !== null) {
      clearTimeout(this.stopTimer);
      this.stopTimer = null;
    }
    if (mode === 'off') {
      this.modeGain.gain.cancelScheduledValues(now);
      this.modeGain.gain.setTargetAtTime(0, now, MUSIC_FADE_TC);
      this.stopTimer = setTimeout(() => {
        this.stopTimer = null;
        if (this.mode === 'off') this.stopScheduler();
      }, MUSIC_FADE_TC * TC_TAIL * 1000);
      return;
    }
    this.active = mode;
    this.modeGain.gain.cancelScheduledValues(now);
    this.modeGain.gain.setTargetAtTime(MUSIC_LEVEL[mode], now, MUSIC_FADE_TC * 0.5);
    this.step = 0;
    this.nextTime = now + SCHEDULER.startDelay;
    this.applyIntensity();
    this.startScheduler();
  }

  setIntensity(level: number): void {
    this.intensity = clamp01(level);
    this.applyIntensity();
  }

  /** Power-up colouring (slowmo: slower/darker/lower; star: faster/brighter). 1,1,1 = neutral. */
  setFeel(rate: number, pitch: number, tone: number): void {
    this.rate = rate > 0.25 && rate < 4 ? rate : 1;
    this.pitch = pitch > 0.25 && pitch < 4 ? pitch : 1;
    this.tone = tone > 0.01 && tone < 4 ? tone : 1;
    this.applyIntensity();
  }

  /** Briefly dip the music (e.g. on crash). */
  duck(depth: number, seconds: number): void {
    const now = this.ctx.currentTime;
    const g = this.duckGain.gain;
    g.cancelScheduledValues(now);
    g.setTargetAtTime(1 - depth, now, 0.02);
    g.setTargetAtTime(1, now + seconds, 0.25);
  }

  dispose(): void {
    this.stopScheduler();
    if (this.stopTimer !== null) clearTimeout(this.stopTimer);
    this.stopTimer = null;
    try {
      this.modeGain.disconnect();
    } catch {
      // ignore
    }
  }

  private applyIntensity(): void {
    const now = this.ctx.currentTime;
    const cutoff =
      this.active === 'game' ? 3200 + 14000 * this.intensity * this.intensity + 1500 : this.active === 'lobby' ? 3800 : 2200;
    this.filter.frequency.setTargetAtTime(Math.min(20000, cutoff * this.tone), now, this.tone < 1 ? 0.12 : 0.3);
  }

  private bpm(): number {
    const base = MUSIC_BPM[this.active];
    return (this.active === 'game' ? base * (1 + INTENSITY_TEMPO_BOOST * this.intensity) : base) * this.rate;
  }

  private hz(midi: number): number {
    return midiToHz(midi) * this.pitch;
  }

  private startScheduler(): void {
    if (this.timer !== null) return;
    this.lastTick = performance.now();
    this.timer = setInterval(() => this.tick(), SCHEDULER.intervalMs);
    this.tick();
  }

  private stopScheduler(): void {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
  }

  private tick(): void {
    if (this.mode === 'off') return;
    const ctx = this.ctx;
    const now = ctx.currentTime;
    // Adaptive lookahead: grow when the main thread stalls, shrink back slowly.
    const wall = performance.now();
    const gap = (wall - this.lastTick) / 1000;
    this.lastTick = wall;
    this.lookahead = Math.min(
      SCHEDULER.maxLookahead,
      Math.max(SCHEDULER.minLookahead, gap > this.lookahead ? gap * 1.5 : this.lookahead * 0.98),
    );
    if (ctx.state !== 'running') {
      this.nextTime = now + SCHEDULER.startDelay;
      return;
    }
    const stepDur = 60 / this.bpm() / 4;
    if (now - this.nextTime > SCHEDULER.maxCatchUp) {
      // Way behind (tab was hidden): resync to the next beat.
      this.step = (Math.ceil(this.step / 4) * 4) % LOOP_STEPS;
      this.nextTime = now + SCHEDULER.startDelay;
    }
    const horizon = now + this.lookahead;
    let guard = 0;
    while (this.nextTime < horizon && guard++ < 64) {
      const swing = this.step % 2 === 1 ? MUSIC_SWING[this.active] * stepDur : 0;
      const t = this.nextTime + swing;
      if (t >= now + SCHEDULER.minLead) this.playStep(this.step, t, stepDur);
      this.nextTime += stepDur;
      this.step = (this.step + 1) % LOOP_STEPS;
    }
  }

  private playStep(step: number, t: number, sd: number): void {
    const s = step % STEPS_PER_BAR;
    const bar = Math.floor(step / STEPS_PER_BAR);
    const chord = CHORDS[bar];
    const hot = this.intensity;
    switch (this.active) {
      case 'game': {
        if (s % 4 === 0) this.kick(t, 1);
        if (s === 4 || s === 12) this.clap(t, 0.7);
        if (s % 4 === 2) this.hat(t, 0.32, true);
        else if (hot > 0.25 || s % 2 === 0) this.hat(t, 0.12 + 0.08 * hot, false);
        // Offbeat octave bass.
        if (s % 2 === 0) this.bass(this.hz(chord.root + (s % 4 === 2 ? 12 : 0)), t, sd * 1.6, 0.8);
        if (s === 3 || s === 6 || s === 10 || s === 14) this.stab(chord, t, sd * 1.2, 0.22);
        const n = LEAD_GAME[step];
        if (n > 0) this.lead(this.hz(n), t, sd * 1.7, 0.16, 'square');
        if (bar === 3 && s >= 12 && hot > 0.5) this.hat(t + sd * 0.5, 0.1, false);
        break;
      }
      case 'lobby': {
        if (s === 0 || s === 10) this.kick(t, 0.7);
        if (s === 12) this.clap(t, 0.35);
        if (s % 4 === 2) this.hat(t, 0.16, false);
        if (s === 0) this.bass(this.hz(chord.root), t, sd * 6, 0.7);
        if (s === 8) this.bass(this.hz(chord.root + 7), t, sd * 3, 0.5);
        if (s === 0) this.pad(chord, t, sd * 15, 0.12);
        const n = LEAD_LOBBY[step];
        if (n > 0) this.lead(this.hz(n + 12), t, sd * 4, 0.1, 'sine');
        break;
      }
      case 'gameover': {
        if (s === 0) this.kick(t, 0.5);
        if (s === 0) this.bass(this.hz(chord.root), t, sd * 12, 0.6);
        if (s === 0) this.pad(chord, t, sd * 15, 0.13);
        const n = LEAD_GAMEOVER[step];
        if (n > 0) this.lead(this.hz(n), t, sd * 6, 0.11, 'triangle');
        break;
      }
    }
  }

  // ---- Instruments (one-shot nodes; disconnected when they end) ---------------

  private finish(src: AudioScheduledSourceNode, ...nodes: AudioNode[]): void {
    src.onended = () => {
      src.disconnect();
      for (const n of nodes) n.disconnect();
    };
  }

  private kick(t: number, vel: number): void {
    const ctx = this.ctx;
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = 'sine';
    glide(o.frequency, t, 160, 45, 0.11);
    const end = perc(g.gain, t, 0.95 * vel, 0.002, 0.11);
    o.connect(g);
    g.connect(this.drumBus);
    o.start(t);
    o.stop(end);
    this.finish(o, g);
  }

  private noiseHit(t: number, type: BiquadFilterType, freq: number, q: number, peak: number, tc: number, dest: AudioNode): void {
    const ctx = this.ctx;
    const s = ctx.createBufferSource();
    s.buffer = this.noise;
    s.loop = true;
    const f = ctx.createBiquadFilter();
    f.type = type;
    f.frequency.value = freq;
    f.Q.value = q;
    const g = ctx.createGain();
    const end = perc(g.gain, t, peak, 0.001, tc);
    s.connect(f);
    f.connect(g);
    g.connect(dest);
    s.start(t, Math.random() * this.noise.duration * 0.9);
    s.stop(end);
    this.finish(s, f, g);
  }

  private clap(t: number, vel: number): void {
    this.noiseHit(t, 'bandpass', 1400, 0.9, 0.55 * vel, 0.012, this.drumBus);
    this.noiseHit(t + 0.012, 'bandpass', 1500, 0.9, 0.6 * vel, 0.07, this.drumBus);
  }

  private hat(t: number, vel: number, open: boolean): void {
    this.noiseHit(t, 'highpass', 7500, 0.7, vel, open ? 0.06 : 0.018, this.drumBus);
  }

  private bass(freq: number, t: number, dur: number, vel: number): void {
    const ctx = this.ctx;
    const o = ctx.createOscillator();
    o.type = 'sawtooth';
    o.frequency.value = freq;
    const f = ctx.createBiquadFilter();
    f.type = 'lowpass';
    f.Q.value = 4;
    f.frequency.setValueAtTime(1400, t);
    f.frequency.setTargetAtTime(280, t, 0.06);
    const g = ctx.createGain();
    const end = ahr(g.gain, t, 0.5 * vel, 0.004, Math.max(0, dur - 0.03), 0.03);
    o.connect(f);
    f.connect(g);
    g.connect(this.bassBus);
    o.start(t);
    o.stop(end);
    this.finish(o, f, g);
  }

  private chordVoices(chord: { root: number; triad: readonly number[] }, t: number, end: number, type: OscillatorType, dest: AudioNode): void {
    for (let i = 0; i < chord.triad.length; i++) {
      for (const detune of [-7, 7]) {
        const o = this.ctx.createOscillator();
        o.type = type;
        o.frequency.value = this.hz(chord.root + 24 + chord.triad[i]);
        o.detune.value = detune;
        o.connect(dest);
        o.start(t);
        o.stop(end);
        this.finish(o);
      }
    }
  }

  private stab(chord: { root: number; triad: readonly number[] }, t: number, dur: number, vel: number): void {
    const ctx = this.ctx;
    const f = ctx.createBiquadFilter();
    f.type = 'lowpass';
    f.frequency.setValueAtTime(3500, t);
    f.frequency.setTargetAtTime(900, t, 0.05);
    const g = ctx.createGain();
    const end = ahr(g.gain, t, vel / 6, 0.003, dur * 0.5, 0.04);
    f.connect(g);
    g.connect(this.synthBus);
    if (this.reverbSend) g.connect(this.reverbSend);
    this.chordVoices(chord, t, end, 'sawtooth', f);
    // Free the shared nodes after the voices end.
    setTimeout(() => {
      f.disconnect();
      g.disconnect();
    }, (end - this.ctx.currentTime) * 1000 + 100);
  }

  private pad(chord: { root: number; triad: readonly number[] }, t: number, dur: number, vel: number): void {
    const ctx = this.ctx;
    const f = ctx.createBiquadFilter();
    f.type = 'lowpass';
    f.frequency.value = 1600;
    const g = ctx.createGain();
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(vel / 6, t + dur * 0.3);
    g.gain.setTargetAtTime(0, t + dur * 0.8, dur * 0.12);
    const end = t + dur * 0.8 + dur * 0.12 * TC_TAIL;
    f.connect(g);
    g.connect(this.synthBus);
    if (this.reverbSend) g.connect(this.reverbSend);
    this.chordVoices(chord, t, end, 'triangle', f);
    setTimeout(() => {
      f.disconnect();
      g.disconnect();
    }, (end - this.ctx.currentTime) * 1000 + 100);
  }

  private lead(freq: number, t: number, dur: number, vel: number, type: OscillatorType): void {
    const ctx = this.ctx;
    const o = ctx.createOscillator();
    o.type = type;
    o.frequency.value = freq;
    const f = ctx.createBiquadFilter();
    f.type = 'lowpass';
    f.frequency.value = type === 'square' ? 2600 : 6000;
    const g = ctx.createGain();
    const end = ahr(g.gain, t, vel, 0.005, Math.max(0, dur * 0.6), dur * 0.25);
    o.connect(f);
    f.connect(g);
    g.connect(this.synthBus);
    if (this.reverbSend) g.connect(this.reverbSend);
    o.start(t);
    o.stop(end);
    this.finish(o, f, g);
  }
}
