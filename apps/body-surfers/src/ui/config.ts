// UI-module tunables (owned by the UI agent).

/** A toast stays on screen this long after `toast.t` (MASTERPLAN §7). */
export const TOAST_MS = 1500;
/** Webcam overlay "JUMP!/SQUAT!/arrow" flash after `lastGesture.t`. */
export const GESTURE_FLASH_MS = 400;

/**
 * Webcam overlay placements. Widths are in "u" = 1% of the largest 16:9 box that fits the
 * viewport (CSS: --u = min(1vw, 1.7778vh)); the matching positions live in webcam.css.
 */
export type CamMode = 'hidden' | 'lobby' | 'countdown' | 'paused' | 'corner' | 'top' | 'mini';
export const CAM_WIDTH_U: Record<CamMode, number> = {
  hidden: 22,
  lobby: 44,
  countdown: 44,
  paused: 44,
  corner: 22,
  top: 21,
  mini: 18,
};
/** Backing-store limits for the webcam canvas. */
export const CAM_MAX_DPR = 2;
export const CAM_MAX_BACKING_W = 1600;
/** Landmarks below this visibility are not drawn. */
export const CAM_MIN_VISIBILITY = 0.35;

export const LEADERBOARD_ROWS_LOBBY = 3;
export const LEADERBOARD_ROWS_GAMEOVER = 6;
export const NAME_MAX_LENGTH = 18;
/** The inline "clear leaderboard?" confirm reverts after this long. */
export const CLEAR_CONFIRM_MS = 5000;
/** Debug overlay text refresh interval. */
export const DEBUG_REFRESH_MS = 250;
/** Power-up bars blink when this many seconds are left. */
export const POWERUP_WARN_S = 2;
