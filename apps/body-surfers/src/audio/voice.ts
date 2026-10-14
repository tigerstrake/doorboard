// One-shot voice bookkeeping: every SFX gets its own output gain (used only for
// stealing / fading) + stereo panner, and lives in a capped pool.
import type { VoiceCategory } from './config';
import { STEAL_TC } from './config';

export interface PooledVoice {
  readonly category: VoiceCategory;
  readonly start: number;
  /** Time after which the voice is silent and can be released. */
  end: number;
  /** Set once the voice has been faded out to make room. Stolen voices don't count toward caps. */
  stolen: boolean;
  kill(now: number): void;
  release(): void;
}

/** Capped set of active voices. When a cap is hit, the oldest voice is faded out ("stolen"). */
export class VoicePool<V extends PooledVoice = PooledVoice> {
  private readonly voices: V[] = [];

  constructor(
    private readonly caps: Readonly<Record<VoiceCategory, number>>,
    private readonly max: number,
  ) {}

  /** Voices still sounding (stolen ones included until their fade ends). */
  get size(): number {
    return this.voices.length;
  }

  /** Live (not stolen) voices, optionally of one category. */
  count(category?: VoiceCategory): number {
    let n = 0;
    for (const v of this.voices) if (!v.stolen && (category === undefined || v.category === category)) n++;
    return n;
  }

  /** Release voices that have finished. */
  prune(now: number): void {
    let w = 0;
    for (let i = 0; i < this.voices.length; i++) {
      const v = this.voices[i];
      if (v.end <= now) v.release();
      else this.voices[w++] = v;
    }
    this.voices.length = w;
  }

  /** Make room for one more voice of `category`, stealing the oldest when a cap is reached. */
  admit(category: VoiceCategory, now: number): void {
    this.prune(now);
    const cap = Math.max(1, this.caps[category]);
    while (this.count(category) >= cap) this.stealOldest(now, category);
    while (this.count() >= this.max) this.stealOldest(now);
  }

  add(v: V): void {
    this.voices.push(v);
  }

  killAll(now: number): void {
    for (const v of this.voices) if (!v.stolen) v.kill(now);
  }

  releaseAll(): void {
    for (const v of this.voices) v.release();
    this.voices.length = 0;
  }

  private stealOldest(now: number, category?: VoiceCategory): void {
    let oldest: V | null = null;
    for (const v of this.voices) {
      if (v.stolen || (category !== undefined && v.category !== category)) continue;
      if (!oldest || v.start < oldest.start) oldest = v;
    }
    if (oldest) oldest.kill(now);
  }
}

/**
 * A one-shot sound: owns its sources and routes them through `out` (gain, untouched
 * except for stealing) → panner → destination, with an optional reverb send.
 */
export class Voice implements PooledVoice {
  readonly out: GainNode;
  private readonly panner: StereoPannerNode | null;
  private sendGain: GainNode | null = null;
  private readonly sources: AudioScheduledSourceNode[] = [];
  end: number;
  stolen = false;
  private released = false;

  constructor(
    readonly ctx: BaseAudioContext,
    dest: AudioNode,
    readonly category: VoiceCategory,
    readonly start: number,
    pan: number,
    readonly noiseBuffer: AudioBuffer,
    private readonly sendDest: AudioNode | null,
  ) {
    this.end = start;
    this.out = ctx.createGain();
    if (typeof ctx.createStereoPanner === 'function') {
      this.panner = ctx.createStereoPanner();
      this.panner.pan.value = pan < -1 ? -1 : pan > 1 ? 1 : pan;
      this.out.connect(this.panner);
      this.panner.connect(dest);
    } else {
      this.panner = null;
      this.out.connect(dest);
    }
  }

  /** The voice's panner param (for sweeps), if panning is supported. */
  get pan(): AudioParam | null {
    return this.panner ? this.panner.pan : null;
  }

  /** Oscillator started at t0 and stopped at t1 (not connected). */
  osc(type: OscillatorType, freq: number, t0: number, t1: number): OscillatorNode {
    const o = this.ctx.createOscillator();
    o.type = type;
    o.frequency.value = freq;
    this.track(o, t0, t1);
    return o;
  }

  /** Looped noise from a random offset, started at t0 and stopped at t1 (not connected). */
  noise(t0: number, t1: number, rate = 1, buffer: AudioBuffer = this.noiseBuffer): AudioBufferSourceNode {
    const s = this.ctx.createBufferSource();
    s.buffer = buffer;
    s.loop = true;
    s.playbackRate.value = rate;
    s.start(t0, Math.random() * buffer.duration * 0.9);
    s.stop(Math.max(t1, t0 + 0.001));
    this.sources.push(s);
    if (t1 > this.end) this.end = t1;
    return s;
  }

  gain(value = 0): GainNode {
    const g = this.ctx.createGain();
    g.gain.value = value;
    return g;
  }

  filter(type: BiquadFilterType, freq: number, q = 0.707): BiquadFilterNode {
    const f = this.ctx.createBiquadFilter();
    f.type = type;
    f.frequency.value = freq;
    f.Q.value = q;
    return f;
  }

  /** Send `level` of the voice output to the reverb. */
  send(level: number): void {
    if (!this.sendDest || level <= 0 || this.sendGain) return;
    this.sendGain = this.gain(level);
    this.out.connect(this.sendGain);
    this.sendGain.connect(this.sendDest);
  }

  /** Extend the voice lifetime (e.g. to cover an effect tail). */
  hold(until: number): void {
    if (until > this.end) this.end = until;
  }

  kill(now: number): void {
    if (this.stolen || this.released) return;
    this.stolen = true;
    this.out.gain.setTargetAtTime(0, now, STEAL_TC);
    const stopAt = now + STEAL_TC * 8;
    for (const s of this.sources) {
      try {
        s.stop(stopAt);
      } catch {
        // already stopped
      }
    }
    this.end = Math.min(this.end, stopAt + 0.01);
  }

  release(): void {
    if (this.released) return;
    this.released = true;
    this.out.disconnect();
    this.panner?.disconnect();
    this.sendGain?.disconnect();
    this.sources.length = 0;
  }

  private track(src: AudioScheduledSourceNode, t0: number, t1: number): void {
    src.start(t0);
    src.stop(Math.max(t1, t0 + 0.001));
    this.sources.push(src);
    if (t1 > this.end) this.end = t1;
  }
}
