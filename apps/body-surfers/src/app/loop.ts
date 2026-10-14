// Fixed-step accumulator (§2.1) and frame statistics.
import { SIM_DT } from '../shared/constants';
import { APP } from './config';

export class FixedStepper {
  private acc = 0;
  /** Tolerance so 1/60 s frames reliably yield 2 × 1/120 s steps despite float error. */
  private readonly eps: number;

  constructor(
    readonly step: number = SIM_DT,
    readonly maxSteps: number = APP.maxSubSteps,
  ) {
    this.eps = step * 1e-6;
  }

  /**
   * Add real time and return how many fixed steps to simulate now (≤ maxSteps).
   * Time beyond maxSteps is dropped (the game slows down instead of spiralling).
   */
  advance(dt: number): number {
    if (!(dt > 0)) return 0;
    this.acc += dt;
    let n = 0;
    while (this.acc + this.eps >= this.step && n < this.maxSteps) {
      this.acc -= this.step;
      n++;
    }
    if (this.acc < 0) this.acc = 0;
    if (n === this.maxSteps && this.acc > this.step) this.acc = this.step;
    return n;
  }

  /** Leftover time (s) not yet simulated. */
  get pending(): number {
    return this.acc;
  }

  reset(): void {
    this.acc = 0;
  }
}

/** Exponential moving averages for the debug overlay. */
export class FrameStats {
  private dtAvg = 1 / 60;
  frameMs = 0;

  get fps(): number {
    return this.dtAvg > 0 ? 1 / this.dtAvg : 0;
  }

  /** Real (uncapped) seconds since the previous frame. */
  tick(dtRaw: number): void {
    if (dtRaw > 0 && dtRaw < 1) this.dtAvg += (dtRaw - this.dtAvg) * 0.05;
  }

  /** Milliseconds of main-thread work spent in the frame. */
  work(ms: number): void {
    if (ms >= 0) this.frameMs += (ms - this.frameMs) * 0.1;
  }
}

/** Rolling 90th percentile of frame deltas (ms) over the last `size` frames. */
export class FrameP90 {
  private readonly buf: Float64Array;
  private readonly sorted: Float64Array;
  private n = 0;
  private i = 0;

  constructor(readonly size = 60) {
    this.buf = new Float64Array(size);
    this.sorted = new Float64Array(size);
  }

  push(ms: number): void {
    this.buf[this.i] = ms;
    this.i = (this.i + 1) % this.size;
    if (this.n < this.size) this.n++;
  }

  reset(): void {
    this.n = 0;
    this.i = 0;
  }

  /** True once the window holds `size` samples. */
  get full(): boolean {
    return this.n >= this.size;
  }

  /** p90 of the samples so far (0 when empty). */
  value(): number {
    if (this.n === 0) return 0;
    const s = this.sorted.subarray(0, this.n);
    s.set(this.buf.subarray(0, this.n));
    s.sort();
    return s[Math.min(this.n - 1, Math.floor(this.n * 0.9))];
  }
}
