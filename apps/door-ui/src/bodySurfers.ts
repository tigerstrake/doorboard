// Body Surfers on the wallboard (ADR-0049): the DoorPad starts it, door-api fans the
// request out on /ws (`wallboard.game_changed`, an ephemeral UI-control message like
// `wallboard.focus_changed`), and the wallboard runs the game in an iframe served
// from the *other* loopback name, so Chromium gives it its own renderer process and
// the wallboard's main thread never waits on pose inference. The wallboard is the
// source of truth for whether a game is showing, and ends it itself on a bell press
// or privacy mode.

/** Build-time switch, on unless VITE_FEATURE_BODY_SURFERS=false (door-api has its own). */
export const FEATURE_BODY_SURFERS =
  (import.meta.env.VITE_FEATURE_BODY_SURFERS as string | undefined) !== "false";

export const BODY_SURFERS_WS_TYPE = "wallboard.game_changed";

/** Where the door-ui build puts the game (scripts/copy-games.mjs). */
export const BODY_SURFERS_PATH = "/games/body-surfers/index.html";

/** door-media binds loopback on the Pi; its CORS allows both kiosk origins. */
const DEFAULT_MEDIA_URL = "http://127.0.0.1:8082/streams";

/** Hard cap on one game, whatever the game itself thinks (a missed exit message). */
export const BODY_SURFERS_MAX_MS = 15 * 60_000;
/** The frame must say "ready" this soon, or it is treated as broken and closed. */
export const BODY_SURFERS_READY_TIMEOUT_MS = 30_000;
/** A hidden leaderboard-clear frame is torn down after this long regardless. */
export const BODY_SURFERS_CLEAR_TIMEOUT_MS = 10_000;
/** The DoorPad "now playing" screen outlives a lost stop message by at most this. */
export const BODY_SURFERS_DOORPAD_TIMEOUT_MS = BODY_SURFERS_MAX_MS;

export type WallboardGameAction = "start" | "stop" | "clear_leaderboard";

export interface WallboardGameMessage {
  action: WallboardGameAction;
  reason: string;
  requestId: string;
  requestedAt: number;
}

const ACTIONS: readonly WallboardGameAction[] = ["start", "stop", "clear_leaderboard"];

/** A `wallboard.game_changed` /ws message → its fields, or null for anything else. */
export function wallboardGameMessageFrom(value: unknown): WallboardGameMessage | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  if (v.type !== BODY_SURFERS_WS_TYPE || v.game !== "body_surfers") return null;
  if (typeof v.action !== "string" || !ACTIONS.includes(v.action as WallboardGameAction)) return null;
  if (typeof v.requestId !== "string" || typeof v.requestedAt !== "number") return null;
  return {
    action: v.action as WallboardGameAction,
    reason: typeof v.reason === "string" ? v.reason : "",
    requestId: v.requestId,
    requestedAt: v.requestedAt,
  };
}

// --- iframe → wallboard (mirrors apps/body-surfers/src/door/bridge.ts) -----------------

export const BRIDGE_CHANNEL = "body-surfers";

export type BodySurfersExitReason = "idle" | "walked_away" | "error";

export type GameToDoor =
  | { type: "ready" }
  | { type: "exit"; reason: BodySurfersExitReason }
  | { type: "leaderboard-cleared" };

const EXIT_REASONS: readonly BodySurfersExitReason[] = ["idle", "walked_away", "error"];

export function gameMessageFrom(data: unknown): GameToDoor | null {
  if (!data || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  if (d.channel !== BRIDGE_CHANNEL) return null;
  if (d.type === "ready" || d.type === "leaderboard-cleared") return { type: d.type };
  if (d.type === "exit") {
    const reason = EXIT_REASONS.includes(d.reason as BodySurfersExitReason)
      ? (d.reason as BodySurfersExitReason)
      : "error";
    return { type: "exit", reason };
  }
  return null;
}

// --- frame URLs ---------------------------------------------------------------------

type PageLocation = { protocol: string; hostname: string; port: string; origin: string };

/**
 * 127.0.0.1 ↔ localhost. They are different *sites* to Chromium, so a frame from the
 * other name gets its own renderer process (site isolation); the same server answers
 * both. Any other host is returned unchanged, and the game's frame-ancestors policy
 * only admits the two loopback origins anyway.
 */
export function siblingLoopbackHost(hostname: string): string {
  if (hostname === "127.0.0.1") return "localhost";
  if (hostname === "localhost") return "127.0.0.1";
  return hostname;
}

export interface BodySurfersUrls {
  game: string;
  clear: string;
  /** Origin the frames' messages must come from. */
  origin: string;
}

/**
 * The game page and the leaderboard-clear page. `override` (VITE_BODY_SURFERS_URL) is a
 * full page URL, e.g. the game's own dev server; its query string is kept, so
 * `?webcam=1` tests door mode with a laptop camera.
 */
export function bodySurfersUrls(
  page: PageLocation,
  env: { override?: string; mediaUrl?: string } = {},
): BodySurfersUrls {
  const port = page.port ? `:${page.port}` : "";
  const base =
    env.override && env.override !== "auto"
      ? env.override
      : `${page.protocol}//${siblingLoopbackHost(page.hostname)}${port}${BODY_SURFERS_PATH}`;
  const game = new URL(base);
  game.searchParams.set("door", "1");
  game.searchParams.set("parent", page.origin);
  game.searchParams.set("media", env.mediaUrl || DEFAULT_MEDIA_URL);
  const clear = new URL("clear.html", game);
  clear.search = "";
  clear.searchParams.set("door", "1");
  clear.searchParams.set("parent", page.origin);
  return { game: game.toString(), clear: clear.toString(), origin: game.origin };
}

export function resolveBodySurfersUrls(): BodySurfersUrls | null {
  if (typeof window === "undefined") return null;
  return bodySurfersUrls(window.location, {
    override: import.meta.env.VITE_BODY_SURFERS_URL as string | undefined,
    mediaUrl: import.meta.env.VITE_BODY_SURFERS_MEDIA_URL as string | undefined,
  });
}

/** What the DoorPad says when door-api refuses a start (its 409 `detail`). */
export function startRefusedMessage(detail: unknown): string {
  if (detail === "privacy_mode") return "The camera is off right now, so the game can't run.";
  if (detail === "visitor_session") return "Someone's at the door. Try again after the visit.";
  if (detail === "body_surfers_disabled") return "Body Surfers is switched off on this door.";
  return "The big screen didn't answer. Try again in a moment.";
}
