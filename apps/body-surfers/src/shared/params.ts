// URL parameter parsing (see AppParams in types.ts for meanings).
import type { AppParams } from './types';

export function parseParams(search: string = typeof location !== 'undefined' ? location.search : ''): AppParams {
  const q = new URLSearchParams(search);
  const flag = (k: string) => q.has(k) && q.get(k) !== '0' && q.get(k) !== 'false';
  const players = q.get('players');
  const seed = q.get('seed');
  const model = q.get('model');
  const quality = q.get('quality');
  return {
    input: q.get('input') === 'keyboard' ? 'keyboard' : 'camera',
    players: players === '1' ? 1 : players === '2' ? 2 : null,
    seed: seed !== null && seed !== '' && Number.isFinite(Number(seed)) ? Number(seed) >>> 0 : null,
    debug: flag('debug'),
    autostart: flag('autostart'),
    autopilot: flag('autopilot'),
    model: model === 'lite' || model === 'full' ? model : null,
    quality: quality === 'low' || quality === 'high' ? quality : null,
    mute: flag('mute'),
  };
}

/** Resolve a public asset path against Vite's BASE_URL (works when deployed under a sub-path). */
export function assetUrl(path: string): string {
  const base = (import.meta.env?.BASE_URL as string | undefined) ?? '/';
  return new URL(path, new URL(base, typeof location !== 'undefined' ? location.href : 'http://localhost/')).href;
}
