// One Euro filter (Casiez et al. 2012): an adaptive low-pass whose cutoff rises with speed,
// so slow jitter is smoothed hard while fast motion stays responsive.
// Speeds are normalized by `invScale` (pass 1/bodyScale) so `beta` is distance-independent.

const TWO_PI = Math.PI * 2;

function alpha(cutoffHz: number, dtS: number): number {
  const tau = 1 / (TWO_PI * cutoffHz);
  return 1 / (1 + tau / dtS);
}

export class OneEuroFilter {
  minCutoff: number;
  beta: number;
  dCutoff: number;
  private x = 0;
  private dx = 0;
  private tPrev = 0;
  private initialized = false;

  constructor(minCutoff: number, beta: number, dCutoff: number) {
    this.minCutoff = minCutoff;
    this.beta = beta;
    this.dCutoff = dCutoff;
  }

  reset(): void {
    this.initialized = false;
    this.dx = 0;
  }

  get value(): number {
    return this.x;
  }

  /**
   * @param value raw sample
   * @param tMs   timestamp (ms), strictly increasing for meaningful output
   * @param invScale multiplier applied to |derivative| before the beta term (e.g. 1/scale)
   */
  filter(value: number, tMs: number, invScale = 1): number {
    if (!this.initialized || !Number.isFinite(this.x)) {
      this.initialized = true;
      this.x = value;
      this.dx = 0;
      this.tPrev = tMs;
      return value;
    }
    const dt = (tMs - this.tPrev) / 1000;
    if (!(dt > 0)) return this.x;
    this.tPrev = tMs;
    if (dt > 1) {
      // Long gap: the old state says nothing about the present.
      this.x = value;
      this.dx = 0;
      return value;
    }
    const rawDx = (value - this.x) / dt;
    this.dx += alpha(this.dCutoff, dt) * (rawDx - this.dx);
    const cutoff = this.minCutoff + this.beta * Math.abs(this.dx) * invScale;
    this.x += alpha(cutoff, dt) * (value - this.x);
    return this.x;
  }
}
