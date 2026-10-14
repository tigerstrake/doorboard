// Gameplay tunables. All numbers are starting values; see MASTERPLAN §4 and the
// game-feel overrides (camera play: ~1.0 s lane-change chain).

export const CONFIG = {
  // ---- Speed curve: v(t) = min + (max - min) * (1 - exp(-t / tau)) ----
  speedMin: 13,
  speedMax: 27,
  speedTau: 90,
  /** Endless linear ramp on top of the curve (m/s gained per second) — the game never stops speeding up. */
  speedRamp: 0.05,
  /** Hard safety cap (m/s) so physics/collision stay sane on very long runs. */
  speedCap: 48,
  /** Past speedMax, emit another speedUp every this many m/s. */
  speedUpEvery: 3,
  /** speedLevel thresholds that emit speedUp (level 1/2/3). */
  speedUpLevels: [0.25, 0.5, 0.75] as readonly number[],

  // ---- Row spacing ----
  /** Time between decision points: lerp(gapStart, gapEnd, level) seconds. */
  gapStart: 1.6,
  gapEnd: 1.05,
  minSpacing: 14,
  /** Reaction time for rows that force a lane change (s), plus per extra lane. */
  reactTime: 1.3,
  reactPerExtraLane: 0.4,
  /** Jump+roll in the same lane need (airtime + roll) * v + this. */
  lowHighExtra: 4,
  /** Zigzag: min seconds between its two rows. */
  zigzagGap: 1.7,
  /** Max consecutive rows containing barriers, and per minute of course time. */
  maxConsecutiveVertical: 3,
  maxVerticalPerMinute: 24,

  // ---- Runner physics ----
  jumpApex: 1.45,
  jumpAirtime: 0.7,
  superApex: 4.0,
  rollTime: 0.65,
  fastFallVy: -22,
  /** Lateral time per lane (s). */
  laneTime: 0.17,
  jumpBuffer: 0.15,
  coyoteTime: 0.1,
  /** A jump within this time after a ground duck cancels the roll (pre-hop dip). */
  duckJumpCancel: 0.3,

  // ---- Collision forgiveness ----
  lateralForgiveness: 0.15,
  nearFaceForgiveness: 0.1,
  /** Within this far below a roof/ramp surface the runner snaps up instead of crashing. */
  roofSnap: 0.45,
  /** Side of a train/ramp higher than feet + this blocks lane changes. */
  sideClearance: 0.45,
  lowBarrierMargin: 0.1,
  /** Front contact during a lane change that overlaps the train sideways by at most this (m) ⇒ side bump / graze, not a crash. */
  cornerForgiveness: 0.25,
  /** Pre-check: block a lane change if a train's near face is within this time of lateral travel. */
  blockLookaheadFrac: 0.5,

  // ---- Moving trains ----
  movingUnlock: 0.15,
  movingSpeedMin: 5,
  movingSpeedMax: 8,
  hornTime: 3.5,

  // ---- Generation ----
  /** Rows are generated while the last far end is within this of the runner. */
  genAhead: 210,
  /** Upper bound of (row.s - runner) at generation time, for moving-train lane reservation. */
  genLeadMax: 300,
  firstRowNoTutorial: 50,
  tutorialEnd: 350,
  coinSpacing: 2.4,
  coinChance: 0.85,
  /** Pattern unlock levels (moving trains: movingUnlock). */
  fullHighUnlock: 0.03,
  rampUnlock: 0.06,
  wallRampUnlock: 0.12,
  zigzagUnlock: 0.3,
  /** At level 1, hard patterns (double/mixed/train_double) weigh (1 + this)×, singles (1 - singleFade)×. */
  hardBoost: 1.2,
  singleFade: 0.35,
  /** First power-up: tutorial end (or 0) + range(firstPowerUpMin, firstPowerUpMax). */
  firstPowerUpMin: 120,
  firstPowerUpMax: 240,
  powerUpMin: 200,
  powerUpMax: 320,
  /** Past nextPowerUp by this much with no free lane ⇒ allow ramp/hurdle lanes. */
  powerUpOverdue: 80,
  powerUpWeights: {
    magnet: 0.18, multiplier: 0.16, sneakers: 0.12, hoverboard: 0.1,
    jetpack: 0.14, slowmo: 0.1, star: 0.1, coinRain: 0.1,
  },

  // ---- Power-ups ----
  magnetDuration: 12,
  magnetRange: 14,
  magnetSpeed: 30,
  multiplierDuration: 15,
  sneakersDuration: 12,
  hoverboardDuration: 25,
  jetpackDuration: 7,
  /** Flight height (feet, m) and rise/hover smoothing rate (1/s). */
  jetpackHeight: 6,
  jetpackRise: 5,
  /** Invulnerability kept during the descent and for this long after touchdown. */
  jetpackLandInvuln: 0.8,
  /** Sky coin trail: lane segment length (m), lead ahead of the runner (m). */
  skyTrailSegment: 36,
  skyTrailLead: 90,
  slowmoDuration: 8,
  /** World scroll factor during slowmo, and seconds to ease in/out. */
  slowmoFactor: 0.6,
  slowmoEase: 0.3,
  starDuration: 8,
  /** Plain invulnerability after the star ends. */
  starGrace: 0.8,
  coinRainDuration: 10,
  /** Coin rain: how far ahead lines are laid (m), and spacing (m). */
  coinRainLead: 110,
  coinRainSpacing: 2.4,
  invulnerableTime: 1.5,
  powerUpWarning: 2,

  // ---- Scoring ----
  coinScore: 10,
  comboGap: 0.6,
  milestoneEvery: 500,

  // ---- Tutorial ----
  hintLead: 2.6,
  /** Tutorial crashes are non-fatal: stumble + this much invulnerability. */
  tutorialInvulnerable: 1.2,

  // ---- Animation ----
  stumbleTime: 0.35,
};

/** g from apex & airtime: apex = g (T/2)^2 / 2. */
export const GRAVITY = (2 * CONFIG.jumpApex) / Math.pow(CONFIG.jumpAirtime / 2, 2);
export const JUMP_V0 = Math.sqrt(2 * GRAVITY * CONFIG.jumpApex);
export const SUPER_V0 = Math.sqrt(2 * GRAVITY * CONFIG.superApex);
