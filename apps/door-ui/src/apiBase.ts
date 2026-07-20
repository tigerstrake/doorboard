// Resolves the door-api base URL shared by the kiosk (App.tsx) and the
// guestbook/poll/admin client (socialApi.ts).
//
// The on-Pi kiosk loads door-ui at http://127.0.0.1:5173 and talks to its
// local door-api on :8080. The owner can also open /admin from a laptop on the
// LAN (e.g. http://door-pi.local:5173) — for that to work the browser must call
// door-api on the *same host it is viewing*, not a fixed 127.0.0.1. So when no
// explicit URL is configured we derive door-api's origin from the page.

// Historical default, used only when there is no browser `window` (SSR / a unit
// test that imports before jsdom is ready) so nothing crashes off-page.
const NO_WINDOW_FALLBACK = "http://127.0.0.1:8000";

type PageLocation = { protocol: string; hostname: string };

/**
 * Resolve the door-api base URL.
 *
 * - An explicit, non-"auto" `VITE_DOOR_API_BASE_URL` is used verbatim
 *   (unchanged deploy/override behavior).
 * - Unset (or the literal "auto") derives from the current page as
 *   `${protocol}//${hostname}:8080`, so the kiosk at localhost reaches
 *   http://localhost:8080 and a laptop at door-pi.local reaches
 *   http://door-pi.local:8080.
 * - With no `window` (SSR/tests), falls back to the historical default.
 */
export function resolveApiBase(
  rawEnv: string | undefined,
  loc?: PageLocation
): string {
  if (rawEnv && rawEnv !== "auto") return rawEnv;
  if (loc && loc.hostname) return `${loc.protocol}//${loc.hostname}:8080`;
  return NO_WINDOW_FALLBACK;
}

/** Derive the door-api WebSocket URL from a resolved HTTP base (http→ws, https→wss). */
export const wsUrlFromApiBase = (base: string): string =>
  `${base.replace(/^http/, "ws")}/ws`;

export const API_BASE = resolveApiBase(
  import.meta.env.VITE_DOOR_API_BASE_URL as string | undefined,
  typeof window !== "undefined" ? window.location : undefined
);
