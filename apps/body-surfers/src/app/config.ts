// App-level tunables (seconds unless noted). Semantics: MASTERPLAN §10.

export const APP = {
  /** Lobby: once someone joined, another visible person gets this long to raise their hands. */
  joinWindowS: 4,
  /** Lobby: a joined person who has been out of view this long is un-joined. */
  joinDropS: 1.5,
  /** 3-2-1 before GO. */
  countdownS: 3,
  /** Countdown: nobody in view this long (camera mode) → start anyway, keyboard arrows steer. */
  noViewFallbackS: 8,
  /** "GO!" stays on screen this long while the run is already live. */
  goFlashS: 0.7,
  /** Crash selfie is taken this long after the crash (face mid-reaction). */
  selfieDelayS: 0.18,
  /** If the capture returns null (face not visible), keep retrying this long. */
  selfieRetryS: 1.0,
  selfieRetryEveryS: 0.1,
  /** All players dead → wait this long (death anim) → gameover. */
  gameoverDelayS: 1.5,
  /** An alive player missing from the camera this long → tracking-lost pause. */
  trackingLostS: 0.7,
  /** tracking-lost: everyone visible again this long → start the resume countdown. */
  trackingBackS: 1.0,
  resumeCountdownS: 3,
  /** Gameover: hands-up / confirm restarts are ignored this long. */
  restartLockoutS: 2.5,
  /** Gameover: every player out of view this long → lobby. */
  gameoverAbsentS: 3,
  /** Gameover: no activity this long → lobby. */
  gameoverIdleS: 25,
  /** ?autopilot=1: restart automatically this long after the lockout ends (endless soak). */
  autopilotRestartS: 2,
  /** A player counts as "still here" for a restart if seen within this window. */
  presentWindowS: 1.5,
  /** Attract world: reset this long after its runner dies. */
  attractResetS: 2,
  /** A vision frame that has not changed for this long is treated as "nobody visible". */
  staleFrameS: 1.0,
  /** Same toast text raised again within this window keeps the original timestamp. */
  toastRepeatS: 1.0,
  /** A sticky toast (keyboard hint, camera lost) is re-raised once it is this old (UI shows a toast 1.5 s). */
  stickyToastS: 1.2,
  /** Adaptive quality: rolling p90 of rAF deltas above this (ms) while playing... */
  slowFrameMs: 22,
  /** ...for this long (1P) lowers graphics for the rest of the page session. 2P lowers at once. */
  slowForS: 2,
  /** rAF deltas above this (ms) are ignored (tab switch, debugger, GC hitch). */
  frameOutlierMs: 250,
  /** Debug overlay numbers refresh interval. */
  debugIntervalS: 0.25,
  /** Booth broadcast: 'state' interval while a run is on screen, 'idle' interval otherwise. */
  boothStateS: 0.25,
  boothIdleS: 2,
  /** Fixed-step loop guards (§2.1). */
  maxSubSteps: 8,
  maxFrameDt: 0.1,
} as const;
