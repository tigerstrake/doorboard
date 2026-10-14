// URL parameter parsing (see AppParams in types.ts for meanings).
import type { AppParams, DoorParams } from './types';

/** door-media binds loopback on the door Pi (deploy/pi-door/doorboard.env.example). */
const DEFAULT_DOOR_MEDIA_URL = 'http://127.0.0.1:8082/streams';

/** An http(s) URL, or null. */
function httpUrl(raw: string | null): URL | null {
  if (!raw) return null;
  try {
    const u = new URL(raw);
    return u.protocol === 'http:' || u.protocol === 'https:' ? u : null;
  } catch {
    return null;
  }
}

function parseDoor(q: URLSearchParams): DoorParams | null {
  const on = q.get('door');
  if (on === null || on === '0' || on === 'false') return null;
  const stream = q.get('stream');
  const parent = httpUrl(q.get('parent'));
  return {
    mediaUrl: httpUrl(q.get('media'))?.href ?? DEFAULT_DOOR_MEDIA_URL,
    stream: stream && /^[A-Za-z0-9_-]{1,64}$/.test(stream) ? stream : 'visitor',
    // Only a bare origin is accepted: it is the postMessage target and the only sender we listen to.
    parentOrigin: parent && parent.href === `${parent.origin}/` ? parent.origin : null,
    webcam: q.get('webcam') === '1',
  };
}

export function parseParams(search: string = typeof location !== 'undefined' ? location.search : ''): AppParams {
  const q = new URLSearchParams(search);
  const flag = (k: string) => q.has(k) && q.get(k) !== '0' && q.get(k) !== 'false';
  const players = q.get('players');
  const seed = q.get('seed');
  const model = q.get('model');
  const quality = q.get('quality');
  const door = parseDoor(q);
  return {
    input: q.get('input') === 'keyboard' ? 'keyboard' : 'camera',
    players: players === '1' ? 1 : players === '2' ? 2 : null,
    seed: seed !== null && seed !== '' && Number.isFinite(Number(seed)) ? Number(seed) >>> 0 : null,
    debug: flag('debug'),
    autostart: flag('autostart'),
    autopilot: flag('autopilot'),
    // The door runs the light model and the low render path unless told otherwise (Pi 5).
    model: model === 'lite' || model === 'full' ? model : door ? 'lite' : null,
    quality: quality === 'low' || quality === 'high' ? quality : door ? 'low' : null,
    mute: flag('mute'),
    door,
  };
}

/** Resolve a public asset path against Vite's BASE_URL (works when deployed under a sub-path). */
export function assetUrl(path: string): string {
  const base = (import.meta.env?.BASE_URL as string | undefined) ?? '/';
  return new URL(path, new URL(base, typeof location !== 'undefined' ? location.href : 'http://localhost/')).href;
}
