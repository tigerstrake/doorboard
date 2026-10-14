// Procedural Web Audio implementation of IAudio (MASTERPLAN §8). No audio files.
// Graph: [music bus, sfx bus, reverb return] → compressor → master (mute) → destination.
// Every method is a silent no-op until unlock() has created the AudioContext.
import type { GameEvent, IAudio, MusicMode, PlayerCount, PlayerId, PowerUpKind, UISound } from '../shared/types';
import type { VoiceCategory } from './config';
import {
  CHARGE,
  COIN,
  COMPRESSOR,
  DEDUPE,
  DEFAULT_MUSIC_VOLUME,
  DEFAULT_SFX_VOLUME,
  JETPACK_RUMBLE,
  MAX_SFX_VOICES,
  MUTE_TC,
  PAN_2P,
  REVERB,
  SFX_LEAD,
  SLOWMO_FEEL,
  STAR_FEEL,
  UNLOCK_TIMEOUT_MS,
  VOICE_CAPS,
  VOLUME_CURVE,
  VOLUME_TC,
} from './config';
import { coinFrequency, coinGain, createImpulseResponse, createNoiseBuffer, glide, midiToHz, perc, ahr, unit } from './dsp';
import { MusicEngine } from './music';
import { Voice, VoicePool } from './voice';

interface Graph {
  ctx: AudioContext;
  master: GainNode;
  musicBus: GainNode;
  sfxBus: GainNode;
  sfxSend: GainNode;
  musicSend: GainNode;
  noise: AudioBuffer;
  music: MusicEngine;
  pool: VoicePool<Voice>;
}

interface Charge {
  oscA: OscillatorNode;
  oscB: OscillatorNode;
  lfo: OscillatorNode;
  gain: GainNode;
}

const POWERUP_ROOT: Readonly<Record<PowerUpKind, number>> = {
  magnet: 72,
  multiplier: 74,
  sneakers: 76,
  hoverboard: 79,
  jetpack: 67,
  slowmo: 64,
  star: 79,
  coinRain: 84,
};

interface Rumble {
  src: AudioBufferSourceNode;
  lfo: OscillatorNode;
  gain: GainNode;
}

type AudioCtor = typeof AudioContext;

function audioCtor(): AudioCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as { AudioContext?: AudioCtor; webkitAudioContext?: AudioCtor };
  return w.AudioContext ?? w.webkitAudioContext ?? null;
}

export class AudioManager implements IAudio {
  private g: Graph | null = null;
  private muted = false;
  private musicVol = DEFAULT_MUSIC_VOLUME;
  private sfxVol = DEFAULT_SFX_VOLUME;
  private musicMode: MusicMode = 'off';
  private intensity = 0;
  private charge: Charge | null = null;
  private chargeLevel = 0;
  private chargeTeardown: ReturnType<typeof setTimeout> | null = null;
  private lastCoinTime = 0;
  private readonly lastByKey = new Map<string, number>();
  private unlocking: Promise<void> | null = null;
  private disposed = false;
  /** Active power-ups as `${playerId}:${kind}` (drives music colouring + jetpack rumble). */
  private readonly activePU = new Set<string>();
  private readonly rumbles = new Map<PlayerId, Rumble>();
  private paused = false;

  get unlocked(): boolean {
    return this.g !== null;
  }

  /** The underlying context (null before unlock) — for diagnostics/sandbox. */
  get context(): AudioContext | null {
    return this.g?.ctx ?? null;
  }

  unlock(): Promise<void> {
    if (this.disposed) return Promise.resolve();
    if (this.g) return this.resume(this.g.ctx);
    if (this.unlocking) return this.unlocking;
    this.unlocking = (async () => {
      try {
        const Ctor = audioCtor();
        if (!Ctor) return;
        const ctx = new Ctor({ latencyHint: 'interactive' });
        this.g = this.build(ctx);
        this.applyVolumes();
        await this.resume(ctx);
        // Re-apply state requested before unlock.
        if (this.musicMode !== 'off') {
          const m = this.musicMode;
          this.musicMode = 'off';
          this.setMusic(m);
        }
        this.g.music.setIntensity(this.intensity);
        if (this.chargeLevel > 0) this.setChargeLevel(this.chargeLevel);
      } catch {
        // Audio unavailable: stay silent.
      } finally {
        this.unlocking = null;
      }
    })();
    return this.unlocking;
  }

  setMuted(m: boolean): void {
    this.muted = !!m;
    const g = this.g;
    if (!g) return;
    g.master.gain.setTargetAtTime(this.muted ? 0 : 1, g.ctx.currentTime, MUTE_TC);
  }

  setVolumes(music: number, sfx: number): void {
    this.musicVol = unit(music, this.musicVol);
    this.sfxVol = unit(sfx, this.sfxVol);
    this.applyVolumes();
  }

  setMusic(mode: MusicMode): void {
    if (mode !== 'off' && mode !== 'lobby' && mode !== 'game' && mode !== 'gameover') return;
    this.musicMode = mode;
    // Leaving the run (game over / lobby) may never send powerUpEnd: drop all power-up state.
    if (mode !== 'game') {
      this.paused = false;
      this.clearPowerUps();
    }
    this.g?.music.setMode(mode);
  }

  setIntensity(level: number): void {
    this.intensity = unit(level, this.intensity);
    this.g?.music.setIntensity(this.intensity);
  }

  setChargeLevel(level: number): void {
    const lv = unit(level, 0);
    this.chargeLevel = lv;
    const g = this.g;
    if (!g) return;
    const now = g.ctx.currentTime;
    if (lv <= 0) {
      if (!this.charge) return;
      this.charge.gain.gain.setTargetAtTime(0, now, CHARGE.tc);
      if (this.chargeTeardown === null) {
        this.chargeTeardown = setTimeout(() => {
          this.chargeTeardown = null;
          if (this.chargeLevel <= 0) this.stopCharge();
        }, CHARGE.teardownAfter * 1000);
      }
      return;
    }
    if (this.chargeTeardown !== null) {
      clearTimeout(this.chargeTeardown);
      this.chargeTeardown = null;
    }
    const c = this.charge ?? this.startCharge(g);
    const f = CHARGE.lowHz * Math.pow(2, CHARGE.octaves * lv);
    c.oscA.frequency.setTargetAtTime(f, now, CHARGE.tc);
    c.oscB.frequency.setTargetAtTime(f * 1.5, now, CHARGE.tc);
    c.lfo.frequency.setTargetAtTime(4 + 12 * lv, now, CHARGE.tc);
    c.gain.gain.setTargetAtTime(CHARGE.peak * (0.35 + 0.65 * lv), now, CHARGE.tc);
  }

  playUi(s: UISound): void {
    const v = this.voice('ui', 0);
    if (!v) return;
    const t = v.start;
    switch (s) {
      case 'click':
        this.tone(v, 'triangle', 1200, t, 0.25, 0.001, 0.02);
        this.tone(v, 'sine', 2400, t, 0.08, 0.001, 0.01);
        break;
      case 'tick':
        this.tone(v, 'square', 880, t, 0.14, 0.002, 0.04, 2400);
        break;
      case 'go': {
        const o = this.tone(v, 'sawtooth', midiToHz(72), t, 0.22, 0.005, 0.25, 3000);
        glide(o.frequency, t, midiToHz(72), midiToHz(84), 0.08);
        this.tone(v, 'square', midiToHz(79), t, 0.12, 0.005, 0.25, 3000);
        v.send(0.3);
        break;
      }
      case 'handsUp':
        this.arp(v, [72, 76, 79], t, 0.06, 'triangle', 0.18, 0.08);
        break;
      case 'join':
        this.arp(v, [76, 83, 88], t, 0.07, 'square', 0.12, 0.09, 3500);
        v.send(0.3);
        break;
      case 'pause': {
        const o = this.tone(v, 'triangle', 660, t, 0.2, 0.004, 0.08);
        glide(o.frequency, t, 660, 440, 0.12);
        break;
      }
      case 'resume': {
        const o = this.tone(v, 'triangle', 440, t, 0.2, 0.004, 0.08);
        glide(o.frequency, t, 440, 660, 0.12);
        break;
      }
      case 'gameOver':
        this.arp(v, [67, 64, 60, 55], t, 0.16, 'triangle', 0.2, 0.18);
        this.tone(v, 'sawtooth', midiToHz(43), t + 0.48, 0.12, 0.01, 0.35, 900);
        v.send(0.4);
        break;
      case 'newRecord':
        this.arp(v, [72, 76, 79, 84], t, 0.09, 'square', 0.12, 0.08, 4000);
        this.chord(v, [84, 88, 91], t + 0.38, 'sawtooth', 0.07, 0.5, 4000);
        this.sparkle(v, t + 0.38, 6);
        v.send(0.45);
        break;
      case 'win':
        this.arp(v, [67, 72, 76, 79, 84], t, 0.08, 'square', 0.12, 0.08, 4000);
        this.chord(v, [72, 76, 79, 84], t + 0.42, 'sawtooth', 0.06, 0.6, 3500);
        this.sparkle(v, t + 0.42, 8);
        v.send(0.45);
        break;
      case 'speedUp':
        this.whooshUp(v, t, 0.5);
        break;
    }
  }

  playGameEvent(e: GameEvent, playerId: PlayerId, playerCount: PlayerCount): void {
    const g = this.g;
    if (!g || !e) return;
    const base = playerCount === 2 ? (playerId === 1 ? PAN_2P : -PAN_2P) : 0;
    switch (e.type) {
      case 'coin':
        this.coin(e.combo, base + (playerCount === 2 ? 0 : clampPan((e.x || 0) * 0.12)));
        break;
      case 'jump': {
        const v = this.voice('jump', base);
        if (!v) return;
        const t = v.start;
        const top = e.super ? 1100 : 700;
        const o = this.tone(v, 'square', 220, t, e.super ? 0.13 : 0.11, 0.004, e.super ? 0.12 : 0.06, 2200);
        glide(o.frequency, t, e.super ? 180 : 240, top, e.super ? 0.28 : 0.14);
        if (e.super) {
          const o2 = this.tone(v, 'triangle', 360, t, 0.12, 0.004, 0.14);
          glide(o2.frequency, t, 360, top * 1.5, 0.3);
          v.send(0.35);
        }
        break;
      }
      case 'land': {
        const v = this.voice('land', base);
        if (!v) return;
        const t = v.start;
        const o = this.tone(v, 'sine', 140, t, e.onRoof ? 0.4 : 0.32, 0.002, 0.05);
        glide(o.frequency, t, e.onRoof ? 160 : 130, 50, 0.08);
        this.noiseBurst(v, t, 'lowpass', e.onRoof ? 1800 : 900, 0.8, 0.12, 0.03);
        break;
      }
      case 'roll': {
        const v = this.voice('roll', base);
        if (!v) return;
        const t = v.start;
        const f = this.noiseBurst(v, t, 'bandpass', 600, 1.5, 0.28, 0.12, 0.06);
        glide(f.frequency, t, 1800, 350, 0.3);
        break;
      }
      case 'laneChange': {
        if (this.throttled(`lane${playerId}`, DEDUPE.laneChange)) return;
        const dir = e.to - e.from >= 0 ? 1 : -1;
        const v = this.voice('move', base);
        if (!v) return;
        const t = v.start;
        const f = this.noiseBurst(v, t, 'bandpass', 2500, 2, 0.16, 0.02, 0.06);
        glide(f.frequency, t, 1500, 4200, 0.12);
        const p = v.pan;
        if (p) {
          p.setValueAtTime(clampPan(base - 0.3 * dir), t);
          p.linearRampToValueAtTime(clampPan(base + 0.3 * dir), t + 0.14);
        }
        break;
      }
      case 'blocked': {
        const v = this.voice('blocked', clampPan(base + 0.35 * e.dir));
        if (!v) return;
        const t = v.start;
        const o = this.tone(v, 'sine', 110, t, 0.4, 0.002, 0.06);
        glide(o.frequency, t, 130, 60, 0.1);
        this.noiseBurst(v, t, 'lowpass', 700, 0.7, 0.25, 0.04);
        // Cartoon boing.
        const b = this.tone(v, 'triangle', 300, t + 0.05, 0.16, 0.005, 0.12);
        b.frequency.setValueAtTime(300, t + 0.05);
        b.frequency.linearRampToValueAtTime(520, t + 0.12);
        b.frequency.linearRampToValueAtTime(260, t + 0.22);
        b.frequency.linearRampToValueAtTime(420, t + 0.3);
        break;
      }
      case 'crash': {
        // A dead runner never sends powerUpEnd (2P: the other player keeps racing).
        this.clearPlayerPowerUps(playerId);
        const v = this.voice('crash', base);
        if (!v) return;
        const t = v.start;
        this.noiseBurst(v, t, 'lowpass', 2600, 0.6, 0.5, 0.18);
        const o = this.tone(v, 'sawtooth', 300, t, 0.2, 0.004, 0.25, 1600);
        glide(o.frequency, t, 320, 50, 0.7);
        this.tone(v, 'sine', 60, t, 0.5, 0.002, 0.15);
        v.send(0.4);
        if (!this.throttled('crashDuck', DEDUPE.crashDuck)) g.music.duck(0.6, 0.6);
        break;
      }
      case 'powerUp': {
        this.setPowerUp(playerId, e.kind, true, base);
        const v = this.voice('powerUp', base);
        if (!v) return;
        this.powerUpPickup(v, e.kind);
        break;
      }
      case 'powerUpWarning': {
        if (this.throttled(`warn${playerId}`, DEDUPE.warning)) return;
        const v = this.voice('warning', base);
        if (!v) return;
        for (let i = 0; i < 3; i++) this.tone(v, 'square', 1320, v.start + i * 0.12, 0.09, 0.002, 0.025, 3000);
        break;
      }
      case 'powerUpEnd': {
        this.setPowerUp(playerId, e.kind, false, base);
        const v = this.voice('powerUp', base);
        if (!v) return;
        const o = this.tone(v, 'triangle', 900, v.start, 0.18, 0.004, 0.1);
        glide(o.frequency, v.start, 900, 260, 0.22);
        break;
      }
      case 'smash': {
        // Several obstacles smashed in the same instant → one hit (keeps the mix intact).
        if (this.throttled(`smash${playerId}`, DEDUPE.smash)) return;
        const v = this.voice('smash', base + (playerCount === 2 ? 0 : (e.lane ?? 0) * 0.3));
        if (!v) return;
        const t = v.start;
        this.noiseBurst(v, t, 'lowpass', 3200, 0.7, 0.42, 0.07);
        const crunch = this.noiseBurst(v, t + 0.01, 'bandpass', 1400, 1.2, 0.3, 0.05);
        glide(crunch.frequency, t + 0.01, 2400, 500, 0.15);
        const o = this.tone(v, 'sine', 120, t, 0.5, 0.002, 0.09);
        glide(o.frequency, t, 160, 40, 0.18);
        const s = this.tone(v, 'square', 220, t, 0.08, 0.002, 0.05, 1800);
        glide(s.frequency, t, 260, 70, 0.12);
        v.send(0.25);
        break;
      }
      case 'hoverboardBreak': {
        const v = this.voice('crash', base);
        if (!v) return;
        const t = v.start;
        this.noiseBurst(v, t, 'highpass', 3500, 0.8, 0.35, 0.09);
        for (const [i, hz] of [2637, 3520, 4186, 3136].entries()) this.tone(v, 'sine', hz, t + i * 0.025, 0.08, 0.001, 0.08);
        const o = this.tone(v, 'sine', 120, t, 0.3, 0.002, 0.06);
        glide(o.frequency, t, 140, 50, 0.1);
        v.send(0.4);
        break;
      }
      case 'trainHorn': {
        const v = this.voice('horn', base + (playerCount === 2 ? 0 : (e.lane ?? 0) * 0.4));
        if (!v) return;
        const t = v.start;
        // Two-tone sawtooth horn (a minor third), two blasts.
        for (const [start, len] of [[0, 0.32], [0.42, 0.55]] as const) {
          for (const hz of [311, 370]) {
            const o = v.osc('sawtooth', hz, t + start, t + start + len + 0.3);
            const f = v.filter('lowpass', 1400, 1.2);
            const gg = v.gain();
            ahr(gg.gain, t + start, 0.07, 0.03, len, 0.04);
            o.connect(f);
            f.connect(gg);
            gg.connect(v.out);
          }
        }
        v.send(0.5);
        break;
      }
      case 'speedUp': {
        if (this.throttled('speedUp', DEDUPE.speedUp)) return;
        const v = this.voice('speedUp', 0);
        if (v) this.whooshUp(v, v.start, 0.6);
        break;
      }
      case 'milestone': {
        if (this.throttled('milestone', DEDUPE.milestone)) return;
        const v = this.voice('milestone', 0);
        if (!v) return;
        for (const [i, n] of [84, 91, 96].entries()) {
          this.tone(v, 'sine', midiToHz(n), v.start + i * 0.07, 0.14, 0.002, 0.35);
          this.tone(v, 'sine', midiToHz(n) * 2.76, v.start + i * 0.07, 0.03, 0.002, 0.12);
        }
        v.send(0.5);
        break;
      }
    }
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    if (this.chargeTeardown !== null) clearTimeout(this.chargeTeardown);
    this.chargeTeardown = null;
    const g = this.g;
    this.g = null;
    if (!g) return;
    try {
      this.stopCharge();
      for (const pid of [...this.rumbles.keys()]) this.stopRumble(pid, true);
      g.music.dispose();
      g.pool.killAll(g.ctx.currentTime);
      g.pool.releaseAll();
      g.master.disconnect();
      void g.ctx.close().catch(() => undefined);
    } catch {
      // ignore
    }
  }

  // ---- Internals ---------------------------------------------------------------

  private build(ctx: AudioContext): Graph {
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = COMPRESSOR.threshold;
    comp.knee.value = COMPRESSOR.knee;
    comp.ratio.value = COMPRESSOR.ratio;
    comp.attack.value = COMPRESSOR.attack;
    comp.release.value = COMPRESSOR.release;
    const master = ctx.createGain();
    master.gain.value = this.muted ? 0 : 1;
    comp.connect(master);
    master.connect(ctx.destination);

    const musicBus = ctx.createGain();
    const sfxBus = ctx.createGain();
    musicBus.connect(comp);
    sfxBus.connect(comp);

    const reverb = ctx.createConvolver();
    reverb.buffer = createImpulseResponse(ctx, REVERB.seconds, REVERB.decay, 7);
    reverb.connect(comp);
    const sfxSend = ctx.createGain();
    const musicSend = ctx.createGain();
    sfxSend.connect(reverb);
    musicSend.connect(reverb);

    const noise = createNoiseBuffer(ctx, 2, 1234);
    const music = new MusicEngine(ctx, musicBus, noise, musicSend);
    const pool = new VoicePool<Voice>(VOICE_CAPS, MAX_SFX_VOICES);
    return { ctx, master, musicBus, sfxBus, sfxSend, musicSend, noise, music, pool };
  }

  private resume(ctx: AudioContext): Promise<void> {
    if (ctx.state === 'running' || ctx.state === 'closed') return Promise.resolve();
    return new Promise<void>((done) => {
      const timer = setTimeout(done, UNLOCK_TIMEOUT_MS);
      ctx
        .resume()
        .catch(() => undefined)
        .then(() => {
          clearTimeout(timer);
          done();
        });
    });
  }

  private applyVolumes(): void {
    const g = this.g;
    if (!g) return;
    const now = g.ctx.currentTime;
    const m = Math.pow(this.musicVol, VOLUME_CURVE);
    const s = Math.pow(this.sfxVol, VOLUME_CURVE);
    g.musicBus.gain.setTargetAtTime(m, now, VOLUME_TC);
    g.sfxBus.gain.setTargetAtTime(s, now, VOLUME_TC);
    g.musicSend.gain.setTargetAtTime(m * REVERB.musicReturn * 0.5, now, VOLUME_TC);
    g.sfxSend.gain.setTargetAtTime(s * REVERB.sfxReturn, now, VOLUME_TC);
  }

  /** True if the event keyed `key` fired within `window` seconds (and records it otherwise). */
  private throttled(key: string, window: number): boolean {
    const g = this.g;
    if (!g) return true;
    const now = g.ctx.currentTime;
    const last = this.lastByKey.get(key);
    if (last !== undefined && now - last < window && now >= last) return true;
    this.lastByKey.set(key, now);
    return false;
  }

  /** New pooled voice, or null when audio is locked/suspended or muted. */
  private voice(cat: VoiceCategory, pan: number, at?: number): Voice | null {
    const g = this.g;
    if (!g || g.ctx.state !== 'running' || this.muted) return null;
    const now = g.ctx.currentTime;
    g.pool.admit(cat, now);
    const v = new Voice(g.ctx, g.sfxBus, cat, at ?? now + SFX_LEAD, clampPan(pan), g.noise, g.sfxSend);
    g.pool.add(v);
    return v;
  }

  /** Oscillator with a percussive envelope into the voice (optional lowpass). */
  private tone(v: Voice, type: OscillatorType, hz: number, t: number, peak: number, attack: number, tc: number, lp?: number): OscillatorNode {
    const gg = v.gain();
    const end = perc(gg.gain, t, peak, attack, tc);
    const o = v.osc(type, hz, t, end);
    if (lp) {
      const f = v.filter('lowpass', lp);
      o.connect(f);
      f.connect(gg);
    } else o.connect(gg);
    gg.connect(v.out);
    return o;
  }

  private noiseBurst(v: Voice, t: number, type: BiquadFilterType, hz: number, q: number, peak: number, tc: number, attack = 0.002): BiquadFilterNode {
    const gg = v.gain();
    const end = perc(gg.gain, t, peak, attack, tc);
    const s = v.noise(t, end);
    const f = v.filter(type, hz, q);
    s.connect(f);
    f.connect(gg);
    gg.connect(v.out);
    return f;
  }

  private arp(v: Voice, notes: readonly number[], t: number, step: number, type: OscillatorType, peak: number, tc: number, lp?: number): void {
    notes.forEach((n, i) => this.tone(v, type, midiToHz(n), t + i * step, peak, 0.003, i === notes.length - 1 ? tc * 2.5 : tc, lp));
  }

  private chord(v: Voice, notes: readonly number[], t: number, type: OscillatorType, peak: number, hold: number, lp: number): void {
    for (const n of notes) {
      for (const det of [-8, 8]) {
        const gg = v.gain();
        const end = ahr(gg.gain, t, peak, 0.01, hold, 0.15);
        const o = v.osc(type, midiToHz(n), t, end);
        o.detune.value = det;
        const f = v.filter('lowpass', lp);
        o.connect(f);
        f.connect(gg);
        gg.connect(v.out);
      }
    }
  }

  private sparkle(v: Voice, t: number, count: number): void {
    for (let i = 0; i < count; i++) {
      const n = 96 + [0, 4, 7, 12, 7, 4, 12, 16][i % 8];
      this.tone(v, 'sine', midiToHz(n), t + i * 0.045, 0.045, 0.001, 0.05);
    }
  }

  private whooshUp(v: Voice, t: number, dur: number): void {
    const f = this.noiseBurst(v, t, 'bandpass', 500, 2.5, 0.22, dur * 0.25, dur * 0.6);
    glide(f.frequency, t, 400, 5000, dur);
    const o = this.tone(v, 'sawtooth', 200, t, 0.05, dur * 0.6, dur * 0.2, 2500);
    glide(o.frequency, t, 200, 800, dur);
  }

  /** Distinct pickup sting per power-up kind. */
  private powerUpPickup(v: Voice, kind: PowerUpKind): void {
    const t = v.start;
    const r = POWERUP_ROOT[kind] ?? 72;
    switch (kind) {
      case 'jetpack': {
        // Rocket ignition: a pop, then a rising roaring whoosh.
        const o = this.tone(v, 'sine', 90, t, 0.4, 0.002, 0.06);
        glide(o.frequency, t, 110, 45, 0.1);
        const f = this.noiseBurst(v, t, 'bandpass', 300, 1.2, 0.3, 0.25, 0.12);
        glide(f.frequency, t, 250, 3800, 0.55);
        const saw = this.tone(v, 'sawtooth', 80, t + 0.02, 0.07, 0.15, 0.2, 1200);
        glide(saw.frequency, t + 0.02, 80, 320, 0.5);
        this.arp(v, [r, r + 7, r + 12], t + 0.3, 0.06, 'square', 0.08, 0.06, 4000);
        v.send(0.3);
        break;
      }
      case 'slowmo': {
        // Time warp: a chord bending down, with a wobbling tape-stop feel.
        for (const [i, n] of [r + 12, r + 7, r].entries()) {
          const o = this.tone(v, 'triangle', midiToHz(n), t + i * 0.02, 0.1, 0.01, 0.25);
          glide(o.frequency, t + i * 0.02, midiToHz(n), midiToHz(n - 12), 0.6);
        }
        const s = this.tone(v, 'sawtooth', midiToHz(r - 12), t, 0.06, 0.02, 0.25, 1500);
        glide(s.frequency, t, midiToHz(r - 12), midiToHz(r - 24), 0.7);
        const f = this.noiseBurst(v, t, 'bandpass', 2000, 3, 0.12, 0.2, 0.05);
        glide(f.frequency, t, 3000, 300, 0.6);
        v.send(0.6);
        break;
      }
      case 'star':
        // Sparkly fanfare.
        this.arp(v, [r - 12, r - 5, r, r + 4], t, 0.07, 'square', 0.1, 0.06, 5000);
        this.chord(v, [r, r + 4, r + 7], t + 0.28, 'sawtooth', 0.05, 0.35, 5000);
        this.sparkle(v, t + 0.2, 8);
        this.sparkle(v, t + 0.42, 6);
        v.send(0.45);
        break;
      case 'coinRain':
        // Cascading chime: a descending pentatonic shower of bell tones.
        [24, 21, 19, 16, 14, 12, 9, 7, 4, 2, 0].forEach((d, i) => {
          const hz = midiToHz(r - 12 + d);
          const at = t + i * 0.045;
          this.tone(v, 'sine', hz, at, 0.09, 0.001, 0.12);
          this.tone(v, 'sine', hz * 2.76, at, 0.025, 0.001, 0.04);
        });
        v.send(0.5);
        break;
      default:
        this.arp(v, [r, r + 4, r + 7, r + 12, r + 16], t, 0.055, 'square', 0.11, 0.07, 4500);
        this.sparkle(v, t + 0.25, 4);
        v.send(0.35);
    }
  }

  private setPowerUp(pid: PlayerId, kind: PowerUpKind, on: boolean, pan: number): void {
    const key = `${pid}:${kind}`;
    if (on) this.activePU.add(key);
    else this.activePU.delete(key);
    if (kind === 'jetpack') {
      if (on) this.startRumble(pid, pan);
      else this.stopRumble(pid);
    }
    if (kind === 'slowmo' || kind === 'star') this.applyFeel();
  }

  clearPowerUps(): void {
    if (this.activePU.size === 0 && this.rumbles.size === 0) return;
    this.activePU.clear();
    for (const pid of [...this.rumbles.keys()]) this.stopRumble(pid);
    this.applyFeel();
  }

  setPaused(paused: boolean): void {
    if (this.paused === paused) return;
    this.paused = paused;
    const g = this.g;
    if (!g) return;
    const now = g.ctx.currentTime;
    for (const r of this.rumbles.values()) {
      r.gain.gain.cancelScheduledValues(now);
      r.gain.gain.setTargetAtTime(paused ? 0 : JETPACK_RUMBLE.peak, now, JETPACK_RUMBLE.tc);
    }
  }

  private clearPlayerPowerUps(pid: PlayerId): void {
    const prefix = `${pid}:`;
    let changed = false;
    for (const k of [...this.activePU]) {
      if (!k.startsWith(prefix)) continue;
      this.activePU.delete(k);
      changed = true;
    }
    this.stopRumble(pid);
    if (changed) this.applyFeel();
  }

  private anyActive(kind: PowerUpKind): boolean {
    for (const k of this.activePU) if (k.endsWith(`:${kind}`)) return true;
    return false;
  }

  /** Slowmo: slower, lower, low-passed music. Star: faster, brighter. Slowmo wins if both. */
  private applyFeel(): void {
    const g = this.g;
    if (!g) return;
    const f = this.anyActive('slowmo') ? SLOWMO_FEEL : this.anyActive('star') ? STAR_FEEL : null;
    if (f) g.music.setFeel(f.rate, f.pitch, f.tone);
    else g.music.setFeel(1, 1, 1);
  }

  /** Quiet looped thrust rumble while a player's jetpack is active. */
  private startRumble(pid: PlayerId, pan: number): void {
    const g = this.g;
    if (!g || this.rumbles.has(pid)) return;
    const ctx = g.ctx;
    const now = ctx.currentTime;
    const src = ctx.createBufferSource();
    src.buffer = g.noise;
    src.loop = true;
    const lp = ctx.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.value = JETPACK_RUMBLE.lowHz;
    lp.Q.value = 1.2;
    const lfo = ctx.createOscillator();
    lfo.frequency.value = 7;
    const lfoDepth = ctx.createGain();
    lfoDepth.gain.value = 40;
    lfo.connect(lfoDepth);
    lfoDepth.connect(lp.frequency);
    const gain = ctx.createGain();
    gain.gain.value = 0;
    if (!this.paused) gain.gain.setTargetAtTime(JETPACK_RUMBLE.peak, now + 0.2, JETPACK_RUMBLE.tc * 3);
    src.connect(lp);
    let tail: AudioNode = lp;
    if (typeof ctx.createStereoPanner === 'function') {
      const p = ctx.createStereoPanner();
      p.pan.value = clampPan(pan);
      lp.connect(p);
      tail = p;
    }
    tail.connect(gain);
    gain.connect(g.sfxBus);
    src.start(now, Math.random() * Math.max(0, g.noise.duration - 0.1));
    lfo.start(now);
    this.rumbles.set(pid, { src, lfo, gain });
  }

  private stopRumble(pid: PlayerId, immediate = false): void {
    const r = this.rumbles.get(pid);
    const g = this.g;
    if (!r) return;
    this.rumbles.delete(pid);
    try {
      const now = g?.ctx.currentTime ?? 0;
      const end = immediate || !g ? now : now + JETPACK_RUMBLE.tc * 8;
      if (g && !immediate) {
        r.gain.gain.cancelScheduledValues(now);
        r.gain.gain.setTargetAtTime(0, now, JETPACK_RUMBLE.tc);
      }
      r.src.stop(end);
      r.lfo.stop(end);
      r.src.onended = () => r.gain.disconnect();
    } catch {
      // ignore
    }
  }

  /** The signature sound: a bright two-note blip whose pitch climbs with the combo. */
  private coin(combo: number, pan: number): void {
    const g = this.g;
    if (!g) return;
    const now = g.ctx.currentTime;
    // Strum coins picked up in the same frame instead of stacking them.
    const t = Math.max(now + SFX_LEAD, this.lastCoinTime + COIN.minInterval);
    if (t - now > COIN.maxQueue) return;
    const v = this.voice('coin', pan, t);
    if (!v) return;
    this.lastCoinTime = t;
    const hz = coinFrequency(combo);
    const k = coinGain(hz);
    this.tone(v, 'square', hz, t, 0.07 * k, 0.001, 0.025, 6000);
    this.tone(v, 'triangle', hz, t, 0.16 * k, 0.001, 0.03);
    this.tone(v, 'square', hz * 1.5, t + 0.045, 0.06 * k, 0.001, 0.07, 7000);
    this.tone(v, 'sine', hz * 2, t + 0.045, 0.1 * k, 0.001, 0.08);
    if (combo >= COIN.hotStreak) this.tone(v, 'sine', hz * 4, t + 0.06, 0.035 * k, 0.001, 0.06);
    v.send(0.15);
  }

  private startCharge(g: Graph): Charge {
    const ctx = g.ctx;
    const gain = ctx.createGain();
    gain.gain.value = 0;
    const trem = ctx.createGain();
    trem.gain.value = 0.75;
    const lfo = ctx.createOscillator();
    lfo.frequency.value = 4;
    const lfoDepth = ctx.createGain();
    lfoDepth.gain.value = 0.25;
    lfo.connect(lfoDepth);
    lfoDepth.connect(trem.gain);
    const oscA = ctx.createOscillator();
    oscA.type = 'triangle';
    oscA.frequency.value = CHARGE.lowHz;
    const oscB = ctx.createOscillator();
    oscB.type = 'sine';
    oscB.frequency.value = CHARGE.lowHz * 1.5;
    oscA.connect(trem);
    oscB.connect(trem);
    trem.connect(gain);
    gain.connect(g.sfxBus);
    oscA.start();
    oscB.start();
    lfo.start();
    this.charge = { oscA, oscB, lfo, gain };
    return this.charge;
  }

  private stopCharge(): void {
    const c = this.charge;
    if (!c) return;
    this.charge = null;
    try {
      c.oscA.stop();
      c.oscB.stop();
      c.lfo.stop();
      c.gain.disconnect();
    } catch {
      // ignore
    }
  }
}

function clampPan(p: number): number {
  return Number.isFinite(p) ? (p < -1 ? -1 : p > 1 ? 1 : p) : 0;
}
