// Inline SVG icons + pictograms. Static, trusted markup only. Colors follow the UI palette;
// pictogram strokes are styled by CSS classes (.bs-o outline, .bs-i fill, .bs-head).

const NAVY = '#1b1b2f';

/** Closed star polygon path (used for bursts / badges). */
export function starPath(cx: number, cy: number, rOuter: number, rInner: number, points: number): string {
  let d = '';
  for (let i = 0; i < points * 2; i++) {
    const r = i % 2 === 0 ? rOuter : rInner;
    const a = (Math.PI * i) / points - Math.PI / 2;
    d += `${i === 0 ? 'M' : 'L'}${(cx + r * Math.cos(a)).toFixed(1)} ${(cy + r * Math.sin(a)).toFixed(1)}`;
  }
  return `${d}Z`;
}

function gearPath(cx: number, cy: number, rOuter: number, rInner: number, teeth: number): string {
  let d = '';
  const step = (Math.PI * 2) / teeth;
  for (let i = 0; i < teeth; i++) {
    const a0 = i * step - step * 0.25;
    const pts: [number, number][] = [
      [a0, rInner],
      [a0 + step * 0.1, rOuter],
      [a0 + step * 0.4, rOuter],
      [a0 + step * 0.5, rInner],
    ];
    for (let j = 0; j < pts.length; j++) {
      const [a, r] = pts[j];
      d += `${i === 0 && j === 0 ? 'M' : 'L'}${(cx + r * Math.cos(a)).toFixed(1)} ${(cy + r * Math.sin(a)).toFixed(1)}`;
    }
  }
  return `${d}Z`;
}

const S = `stroke="${NAVY}" stroke-linejoin="round" stroke-linecap="round"`;

export const ICONS = {
  coin: `<svg viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="27" fill="#ffb300" ${S} stroke-width="5"/><circle cx="32" cy="32" r="18.5" fill="#ffd23f" stroke="#e08e00" stroke-width="3.5"/><path d="M32 22 L35 29 L42 29.5 L36.5 34 L38.5 41 L32 37 L25.5 41 L27.5 34 L22 29.5 L29 29 Z" fill="#fff6c4" stroke="#e08e00" stroke-width="2" stroke-linejoin="round"/></svg>`,

  magnet: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M12 9 H26 V33 A6 6 0 0 0 38 33 V9 H52 V33 A20 20 0 0 1 12 33 Z" fill="#ff4757" ${S} stroke-width="4.5"/><path d="M12 9 H26 V19 H12 Z M38 9 H52 V19 H38 Z" fill="#e9eef5" ${S} stroke-width="4.5"/><path d="M17 36 Q19 44 26 47" fill="none" stroke="#fff" stroke-width="3.5" stroke-linecap="round" opacity=".7"/></svg>`,

  multiplier: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="${starPath(32, 32, 29, 21, 12)}" fill="#2ed573" ${S} stroke-width="4"/><text x="32" y="41.5" text-anchor="middle" font-family="'Lilita One', sans-serif" font-size="25" fill="#fff" stroke="${NAVY}" stroke-width="5" paint-order="stroke" stroke-linejoin="round">2×</text></svg>`,

  sneakers: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M9 21 L1 15 M10 28 L0 26" stroke="${NAVY}" stroke-width="4" stroke-linecap="round"/><path d="M8 45 C8 31 11 23 17 18 L27 22 C29 28 35 31 41 32 L52 35 C58 37 61 41 61 46 V48 H8 Z" fill="#1e90ff" ${S} stroke-width="4.5"/><path d="M8 48 H61 V51 Q61 56 56 56 H13 Q8 56 8 51 Z" fill="#fff" ${S} stroke-width="4.5"/><path d="M26 28 L32 25 M30 33 L36 30" stroke="#fff" stroke-width="3.5" stroke-linecap="round"/><path d="M40 40 H54" stroke="#ffd23f" stroke-width="3.5" stroke-linecap="round"/></svg>`,

  hoverboard: `<svg viewBox="0 0 64 64" aria-hidden="true"><g transform="rotate(-18 32 30)"><rect x="3" y="22" width="58" height="17" rx="8.5" fill="#a55eea" ${S} stroke-width="4.5"/><path d="M15 30.5 H49" stroke="#ffd23f" stroke-width="4.5" stroke-linecap="round"/></g><path d="M12 52 H20 M28 57 H38 M46 51 H54" stroke="#53e8ff" stroke-width="4.5" stroke-linecap="round"/></svg>`,

  jetpack: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M17 46 Q20 58 23 62 Q26 58 29 46 Z M35 46 Q38 58 41 62 Q44 58 47 46 Z" fill="#ff9f1a" ${S} stroke-width="3.5"/><path d="M20 47 Q23 54 23 56 M38 47 Q41 54 41 56" stroke="#ffd23f" stroke-width="3" stroke-linecap="round" fill="none"/><rect x="13" y="10" width="18" height="36" rx="9" fill="#ff4757" ${S} stroke-width="4.5"/><rect x="33" y="10" width="18" height="36" rx="9" fill="#ff4757" ${S} stroke-width="4.5"/><rect x="26" y="20" width="12" height="16" rx="3" fill="#e9eef5" ${S} stroke-width="4"/><path d="M18 17 V30 M38 17 V30" stroke="#fff" stroke-width="3.5" stroke-linecap="round" opacity=".7"/></svg>`,

  slowmo: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M14 5 H50 M14 59 H50" stroke="${NAVY}" stroke-width="6" stroke-linecap="round"/><path d="M17 8 H47 C47 22 37 26 35 32 C37 38 47 42 47 56 H17 C17 42 27 38 29 32 C27 26 17 22 17 8 Z" fill="#cfefff" ${S} stroke-width="4.5"/><path d="M23 16 H41 C39 22 34 25 32 29 C30 25 25 22 23 16 Z" fill="#53e8ff" stroke="#1e90ff" stroke-width="2" stroke-linejoin="round"/><path d="M32 33 V44 M21 52 C24 46 40 46 43 52 Z" fill="#1e90ff" stroke="#1e90ff" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/><path d="M21 12 V18" stroke="#fff" stroke-width="3" stroke-linecap="round"/></svg>`,

  star: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="${starPath(32, 34, 29, 13, 5)}" fill="#ffd23f" ${S} stroke-width="4.5"/><path d="${starPath(32, 34, 17, 8, 5)}" fill="#ff9f1a" opacity=".55"/><circle cx="26" cy="32" r="3" fill="${NAVY}"/><circle cx="38" cy="32" r="3" fill="${NAVY}"/><path d="M22 20 L26 15" stroke="#fff" stroke-width="3.5" stroke-linecap="round"/></svg>`,

  coinRain: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M14 30 A9 9 0 0 1 17 13 A13 13 0 0 1 41 10 A10 10 0 0 1 54 20 A8 8 0 0 1 50 34 H16 A8 8 0 0 1 14 30 Z" fill="#e9eef5" ${S} stroke-width="4.5"/><path d="M22 18 Q25 14 30 14" stroke="#fff" stroke-width="3" stroke-linecap="round" fill="none"/><circle cx="19" cy="44" r="6" fill="#ffd23f" ${S} stroke-width="3.5"/><circle cx="34" cy="53" r="6" fill="#ffd23f" ${S} stroke-width="3.5"/><circle cx="48" cy="44" r="6" fill="#ffd23f" ${S} stroke-width="3.5"/><path d="M19 41 V47 M34 50 V56 M48 41 V47" stroke="#e08e00" stroke-width="2.5" stroke-linecap="round"/></svg>`,

  crown: `<svg viewBox="0 0 64 54" aria-hidden="true"><path d="M7 42 L3 11 L19 24 L32 4 L45 24 L61 11 L57 42 Z" fill="#ffd23f" ${S} stroke-width="4.5"/><rect x="6" y="40" width="52" height="10" rx="2.5" fill="#ffb300" ${S} stroke-width="4.5"/><circle cx="32" cy="29" r="4.5" fill="#ff4757" ${S} stroke-width="3"/><circle cx="17" cy="32" r="3.2" fill="#1e90ff" ${S} stroke-width="2.5"/><circle cx="47" cy="32" r="3.2" fill="#2ed573" ${S} stroke-width="2.5"/></svg>`,

  trophy: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M18 13 H8 V18 A10 10 0 0 0 19 28 M46 13 H56 V18 A10 10 0 0 1 45 28" fill="none" ${S} stroke-width="4.5"/><path d="M17 7 H47 V23 A15 15 0 0 1 17 23 Z" fill="#ffd23f" ${S} stroke-width="4.5"/><path d="M27 37 H37 V46 H27 Z" fill="#ffb300" ${S} stroke-width="4.5"/><rect x="17" y="46" width="30" height="11" rx="2.5" fill="#1e90ff" ${S} stroke-width="4.5"/><path d="M25 13 V22" stroke="#fff" stroke-width="3.5" stroke-linecap="round"/></svg>`,

  camera: `<svg viewBox="0 0 64 64" aria-hidden="true"><rect x="5" y="17" width="40" height="31" rx="7" fill="#1e90ff" ${S} stroke-width="4.5"/><path d="M45 28 L59 19 V46 L45 37 Z" fill="#1e90ff" ${S} stroke-width="4.5"/><circle cx="16" cy="27" r="4" fill="#fff"/></svg>`,

  cameraOff: `<svg viewBox="0 0 64 64" aria-hidden="true"><rect x="5" y="17" width="40" height="31" rx="7" fill="#1e90ff" ${S} stroke-width="4.5"/><path d="M45 28 L59 19 V46 L45 37 Z" fill="#1e90ff" ${S} stroke-width="4.5"/><path d="M9 7 L55 57" stroke="${NAVY}" stroke-width="11" stroke-linecap="round"/><path d="M9 7 L55 57" stroke="#ff4757" stroke-width="5" stroke-linecap="round"/></svg>`,

  pause: `<svg viewBox="0 0 64 64" aria-hidden="true"><rect x="15" y="12" width="12" height="40" rx="4" fill="#fff" ${S} stroke-width="4.5"/><rect x="37" y="12" width="12" height="40" rx="4" fill="#fff" ${S} stroke-width="4.5"/></svg>`,

  play: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M20 11 L52 32 L20 53 Z" fill="#fff" ${S} stroke-width="5"/></svg>`,

  gear: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="${gearPath(32, 32, 27, 20, 8)}" fill="#fff" ${S} stroke-width="4.5"/><circle cx="32" cy="32" r="8" fill="#1e90ff" ${S} stroke-width="4.5"/></svg>`,

  fullscreen: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M10 24 V10 H24 M40 10 H54 V24 M54 40 V54 H40 M24 54 H10 V40" fill="none" stroke="${NAVY}" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/><path d="M10 24 V10 H24 M40 10 H54 V24 M54 40 V54 H40 M24 54 H10 V40" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/></svg>`,

  speaker: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M8 24 H18 L32 12 V52 L18 40 H8 Z" fill="#fff" ${S} stroke-width="4.5"/><path d="M41 22 Q48 32 41 42 M48 15 Q60 32 48 49" fill="none" stroke="${NAVY}" stroke-width="5" stroke-linecap="round"/></svg>`,

  check: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M13 34 L27 48 L52 18" fill="none" stroke="${NAVY}" stroke-width="15" stroke-linecap="round" stroke-linejoin="round"/><path d="M13 34 L27 48 L52 18" fill="none" stroke="#fff" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/></svg>`,

  close: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M18 18 L46 46 M46 18 L18 46" stroke="${NAVY}" stroke-width="15" stroke-linecap="round"/><path d="M18 18 L46 46 M46 18 L18 46" stroke="#fff" stroke-width="7" stroke-linecap="round"/></svg>`,

  eye: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M4 32 Q32 6 60 32 Q32 58 4 32 Z" fill="#fff" ${S} stroke-width="4.5"/><circle cx="32" cy="32" r="10" fill="#1e90ff" ${S} stroke-width="4"/><circle cx="35" cy="29" r="3" fill="#fff"/></svg>`,

  space: `<svg viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="27" fill="#ffd23f" ${S} stroke-width="4.5" stroke-dasharray="7 6"/><circle cx="32" cy="20" r="5.5" fill="#fff" ${S} stroke-width="3.5"/><path d="M32 27 V41 M20 31 H44 M32 41 L26 51 M32 41 L38 51" fill="none" stroke="${NAVY}" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`,

  arrowLeft: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M6 32 L30 10 V22 H56 V42 H30 V54 Z" fill="#ffd23f" ${S} stroke-width="5"/></svg>`,
  arrowRight: `<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M58 32 L34 10 V22 H8 V42 H34 V54 Z" fill="#ffd23f" ${S} stroke-width="5"/></svg>`,

  burst: `<svg viewBox="0 0 100 100" aria-hidden="true"><path d="${starPath(50, 50, 49, 38, 18)}" fill="#ff4757" ${S} stroke-width="3.5"/></svg>`,
  medal: `<svg viewBox="0 0 100 100" aria-hidden="true"><path d="${starPath(50, 50, 48, 41, 14)}" fill="#1e90ff" ${S} stroke-width="4"/></svg>`,
} as const;

// ---------------------------------------------------------------------------
// Pictograms (120×120 stick figures). Limbs are drawn twice: navy outline, white fill.
// ---------------------------------------------------------------------------

function figure(limbs: string, hx: number, hy: number, cls = 'bs-fig'): string {
  return `<g class="${cls}"><path class="bs-o" d="${limbs}"/><path class="bs-i" d="${limbs}"/><circle class="bs-head" cx="${hx}" cy="${hy}" r="11"/></g>`;
}

const ground = (cx: number) => `<ellipse class="bs-shadow" cx="${cx}" cy="111" rx="22" ry="5"/>`;
const upArrow = (x: number) =>
  `<path class="bs-arrow bs-arrow--green" d="M${x} 10 L${x + 15} 30 H${x + 7} V52 H${x - 7} V30 H${x - 15} Z"/>`;
const downArrow = (x: number) =>
  `<path class="bs-arrow bs-arrow--orange" d="M${x - 7} 8 H${x + 7} V30 H${x + 15} L${x} 50 L${x - 15} 30 H${x - 7} Z"/>`;
const leftArrow = (x: number, y: number) =>
  `<path class="bs-arrow bs-arrow--yellow" d="M${x} ${y} L${x + 22} ${y - 20} V${y - 9} H${x + 42} V${y + 9} H${x + 22} V${y + 20} Z"/>`;
const rightArrow = (x: number, y: number) =>
  `<path class="bs-arrow bs-arrow--yellow" d="M${x} ${y} L${x - 22} ${y - 20} V${y - 9} H${x - 42} V${y + 9} H${x - 22} V${y + 20} Z"/>`;

const PICTO_OPEN = '<svg class="bs-picto-svg" viewBox="0 0 120 120" aria-hidden="true">';
const HANDS_UP_ARMS = 'M60 56 L43 42 L38 18 M60 56 L77 42 L82 18';
const HANDS_UP_BODY = 'M60 50 L60 81 M60 81 L50 107 M60 81 L70 107';

export const PICTOS = {
  jump:
    PICTO_OPEN +
    ground(60) +
    upArrow(18) +
    upArrow(102) +
    `<g class="bs-fig-move">` +
    `<path class="bs-motion" d="M50 88 V97 M60 91 V102 M70 88 V97"/>` +
    figure('M60 29 L60 58 M60 36 L44 20 M60 36 L76 20 M60 58 L47 67 L53 81 M60 58 L73 67 L67 81', 60, 16) +
    `</g></svg>`,

  duck:
    PICTO_OPEN +
    ground(60) +
    downArrow(18) +
    downArrow(102) +
    `<g class="bs-fig-move">` +
    figure('M60 58 L60 82 M60 64 L43 77 M60 64 L77 77 M60 82 L42 86 L44 106 M60 82 L78 86 L76 106', 60, 45) +
    `</g></svg>`,

  left:
    PICTO_OPEN +
    ground(76) +
    leftArrow(4, 62) +
    `<g class="bs-fig-move">` +
    figure('M76 35 L76 68 M76 42 L62 58 M76 42 L90 58 M76 68 L63 104 M76 68 L89 104', 76, 22) +
    `</g></svg>`,

  right:
    PICTO_OPEN +
    ground(44) +
    rightArrow(116, 62) +
    `<g class="bs-fig-move">` +
    figure('M44 35 L44 68 M44 42 L30 58 M44 42 L58 58 M44 68 L31 104 M44 68 L57 104', 44, 22) +
    `</g></svg>`,

  switch:
    PICTO_OPEN +
    ground(60) +
    `<g transform="translate(0 6) scale(.62)">${leftArrow(2, 90)}</g>` +
    `<g transform="translate(45.6 6) scale(.62)">${rightArrow(118, 90)}</g>` +
    `<g class="bs-fig-move">` +
    figure('M60 35 L60 68 M60 42 L46 58 M60 42 L74 58 M60 68 L50 104 M60 68 L70 104', 60, 22) +
    `</g></svg>`,

  handsUp:
    PICTO_OPEN +
    ground(60) +
    `<path class="bs-spark" d="${starPath(22, 16, 9, 4, 4)}"/><path class="bs-spark bs-spark--b" d="${starPath(99, 14, 8, 3.5, 4)}"/>` +
    `<g class="bs-fig-move"><g class="bs-fig">` +
    `<g class="bs-arms"><path class="bs-o" d="${HANDS_UP_ARMS}"/></g>` +
    `<path class="bs-o" d="${HANDS_UP_BODY}"/>` +
    `<g class="bs-arms"><path class="bs-i" d="${HANDS_UP_ARMS}"/></g>` +
    `<path class="bs-i" d="${HANDS_UP_BODY}"/>` +
    `<circle class="bs-head" cx="60" cy="37" r="11"/>` +
    `</g></g></svg>`,
} as const;

export type PictoKind = keyof typeof PICTOS;
