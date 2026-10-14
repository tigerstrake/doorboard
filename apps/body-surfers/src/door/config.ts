// Door mode (ADR-0049): Body Surfers inside the doorboard wallboard, reading the
// visitor camera that door-media already streams. Seconds unless noted.

export const DOOR = {
  /** The lobby gives up after this long without a run starting. */
  lobbyMaxS: 60,
  /** Nobody in view this long, on any screen → hand the wallboard back. */
  nobodyS: 20,
  /** An error card stays up this long (so someone can read it), then the game exits. */
  errorDwellS: 8,
  /** Still booting (model + camera) after this long → treat as an error. */
  bootMaxS: 45,
  /** WHEP must deliver a stream within this long. */
  cameraTimeoutS: 10,
  /** Pose inference at most every 50 ms (20 Hz): the Pi is also software-encoding the camera. */
  minInferenceIntervalMs: 50,
  /** The visitor camera runs at 25 fps by default; the laptop "face a light" hint starts below 24. */
  lowFpsThreshold: 15,
  /** Leaderboard entries (and their crash photos) older than this are dropped. */
  leaderboardMaxAgeDays: 30,
} as const;

/** Shown in the lobby, before anyone plays: the selfie is part of the deal. */
export const DOOR_NOTICE = 'Crash photos go on this screen’s leaderboard for 30 days.';

/** Error-message name for the visitor stream. */
export const DOOR_CAMERA_LABEL = 'The door camera';
