// Fun placeholder "crash face" avatars for leaderboard entries / results without a photo.
// Deterministic per seed (entry id or name), cached as SVG data URLs.

const BGS = ['#ffd23f', '#1e90ff', '#ff4757', '#2ed573', '#a55eea', '#ff7a1a'];
const SKINS = ['#ffdcb5', '#f3c48f', '#e0a96d', '#c27c4a', '#8d5524', '#5c3a1e'];
const HAIR = ['#1b1b2f', '#6b3e26', '#f4c430', '#d35400', '#ff6fb5', '#3d8bff'];
const NAVY = '#1b1b2f';

const cache = new Map<string, string>();

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function eyes(kind: number): string {
  const st = `stroke="${NAVY}" stroke-width="4.5" stroke-linecap="round" fill="none"`;
  switch (kind) {
    case 0: // X_X
      return `<path d="M32 47 l9 9 M41 47 l-9 9 M55 47 l9 9 M64 47 l-9 9" ${st}/>`;
    case 1: // dizzy spirals
      return `<path d="M37 52 m-6 0 a6 6 0 1 0 6 -6 a3.5 3.5 0 1 0 3 3.5 M60 52 m-6 0 a6 6 0 1 0 6 -6 a3.5 3.5 0 1 0 3 3.5" ${st} stroke-width="3.5"/>`;
    case 2: // one huge, one squint
      return `<circle cx="37" cy="51" r="8" fill="#fff" stroke="${NAVY}" stroke-width="4"/><circle cx="39" cy="52" r="3.5" fill="${NAVY}"/><path d="M53 52 q7 -6 13 0" ${st}/>`;
    default: // shocked
      return `<circle cx="37" cy="51" r="6.5" fill="#fff" stroke="${NAVY}" stroke-width="4"/><circle cx="59" cy="51" r="6.5" fill="#fff" stroke="${NAVY}" stroke-width="4"/><circle cx="37" cy="52" r="2.5" fill="${NAVY}"/><circle cx="59" cy="52" r="2.5" fill="${NAVY}"/>`;
  }
}

function mouth(kind: number): string {
  switch (kind) {
    case 0: // wobbly
      return `<path d="M36 70 q4 -5 8 0 t8 0 t8 0" stroke="${NAVY}" stroke-width="4.5" stroke-linecap="round" fill="none"/>`;
    case 1: // O
      return `<ellipse cx="48" cy="71" rx="6" ry="7.5" fill="#7a1f2b" stroke="${NAVY}" stroke-width="4"/>`;
    default: // tongue out
      return `<path d="M37 67 q11 9 22 0" stroke="${NAVY}" stroke-width="4.5" stroke-linecap="round" fill="none"/><path d="M50 70 q1 10 7 9 q5 -1 3 -11" fill="#ff6b81" stroke="${NAVY}" stroke-width="3.5" stroke-linejoin="round"/>`;
  }
}

function hair(kind: number, color: string): string {
  const st = `fill="${color}" stroke="${NAVY}" stroke-width="4" stroke-linejoin="round"`;
  switch (kind) {
    case 0: // backwards cap
      return `<path d="M20 44 Q22 18 48 18 Q74 18 76 44 Z" ${st}/><path d="M66 40 H86 Q88 46 82 47 H66 Z" ${st}/>`;
    case 1: // spiky
      return `<path d="M22 46 L24 28 L32 34 L36 18 L44 30 L50 14 L56 30 L64 18 L66 34 L74 28 L74 46 Q48 34 22 46 Z" ${st}/>`;
    default: // tuft
      return `<path d="M24 44 Q26 22 48 22 Q70 22 72 44 Q60 32 48 34 Q38 32 24 44 Z" ${st}/><path d="M46 22 q-2 -9 6 -12" fill="none" stroke="${NAVY}" stroke-width="4" stroke-linecap="round"/>`;
  }
}

export function placeholderAvatar(seed: string): string {
  const hit = cache.get(seed);
  if (hit) return hit;
  const r = hash(seed || 'surfer');
  const bg = BGS[r % BGS.length];
  const skin = SKINS[(r >>> 3) % SKINS.length];
  const hairColor = HAIR[(r >>> 7) % HAIR.length];
  const markup =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96">` +
    `<rect width="96" height="96" fill="${bg}"/>` +
    `<g fill="#fff" opacity=".22"><path d="M48 48 L40 -10 H56 Z"/><path d="M48 48 L106 36 V54 Z"/><path d="M48 48 L58 106 H40 Z"/><path d="M48 48 L-10 58 V40 Z"/></g>` +
    `<ellipse cx="48" cy="95" rx="30" ry="14" fill="${hairColor}" stroke="${NAVY}" stroke-width="4"/>` +
    `<circle cx="48" cy="54" r="27" fill="${skin}" stroke="${NAVY}" stroke-width="4"/>` +
    hair((r >>> 11) % 3, hairColor) +
    eyes((r >>> 14) % 4) +
    mouth((r >>> 17) % 3) +
    `<path d="M16 22 l3 7 7 1 -5 5 1 7 -6 -3 -6 3 1 -7 -5 -5 7 -1 Z" fill="#ffd23f" stroke="${NAVY}" stroke-width="2.5" stroke-linejoin="round"/>` +
    `<path d="M78 14 l2 5 5 .5 -4 3.5 1 5 -4 -2.5 -4 2.5 1 -5 -4 -3.5 5 -.5 Z" fill="#fff" stroke="${NAVY}" stroke-width="2.5" stroke-linejoin="round"/>` +
    `</svg>`;
  const url = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(markup)}`;
  cache.set(seed, url);
  return url;
}
