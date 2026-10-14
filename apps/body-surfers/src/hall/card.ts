// Trading-card renderer for the Hall of Fame: LeaderboardEntry -> PNG data URL.
// Pure with respect to app state (only reads the entry, rank and the clock).
import type { LeaderboardEntry } from '../shared/types';
import { formatNum, relativeDate } from './data';

/** Logical card size; the bitmap is rendered at CARD_SCALE× for crisp TV output. */
export const CARD_W = 360;
export const CARD_H = 520;
const CARD_SCALE = 1.5;

const NAVY = '#1b1b2f';
const YELLOW = '#ffd23f';
const BLUE = '#1e90ff';
const RED = '#ff4757';
const GREEN = '#2ed573';
const DISPLAY = '"Lilita One", "Arial Black", sans-serif';
const BODY = '"Nunito", "Arial Rounded MT Bold", sans-serif';

interface Tier {
  top: string;
  bottom: string;
  medal: string;
  medalHi: string;
  ribbon: string;
}

function tierFor(rank: number): Tier {
  if (rank === 1) return { top: '#fff09a', bottom: '#ffb400', medal: YELLOW, medalHi: '#fff6c2', ribbon: RED };
  if (rank === 2) return { top: '#f4f8fc', bottom: '#9fb3c8', medal: '#d9e2ec', medalHi: '#ffffff', ribbon: BLUE };
  if (rank === 3) return { top: '#ffd0a6', bottom: '#d9773a', medal: '#f0a060', medalHi: '#ffd9b8', ribbon: GREEN };
  return { top: '#7cc8ff', bottom: BLUE, medal: '#ffffff', medalHi: '#ffffff', ribbon: RED };
}

let fontsReady: Promise<void> | null = null;
/** Resolves once the card fonts are usable on canvas (never rejects). */
export function ensureFonts(): Promise<void> {
  if (!fontsReady) {
    const fonts = typeof document !== 'undefined' ? document.fonts : undefined;
    fontsReady = fonts
      ? Promise.all([
          fonts.load(`64px "Lilita One"`),
          fonts.load(`900 20px "Nunito"`),
          fonts.load(`800 20px "Nunito"`),
        ]).then(
          () => undefined,
          () => undefined,
        )
      : Promise.resolve();
  }
  return fontsReady;
}

function loadImage(src: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const img = new Image();
    const timer = window.setTimeout(() => resolve(null), 4000);
    img.onload = () => {
      window.clearTimeout(timer);
      resolve(img.naturalWidth > 0 ? img : null);
    };
    img.onerror = () => {
      window.clearTimeout(timer);
      resolve(null);
    };
    img.src = src;
  });
}

type Ctx = CanvasRenderingContext2D;

function outlinedText(ctx: Ctx, text: string, x: number, y: number, fill: string, stroke: number): void {
  ctx.lineJoin = 'round';
  ctx.miterLimit = 2;
  ctx.lineWidth = stroke;
  ctx.strokeStyle = NAVY;
  ctx.strokeText(text, x, y);
  ctx.fillStyle = fill;
  ctx.fillText(text, x, y);
}

function fitFont(ctx: Ctx, text: string, maxW: number, size: number, family: string, weight = ''): number {
  let s = size;
  for (; s > 10; s -= 2) {
    ctx.font = `${weight} ${s}px ${family}`.trim();
    if (ctx.measureText(text).width <= maxW) break;
  }
  return s;
}

function star(ctx: Ctx, cx: number, cy: number, points: number, outer: number, inner: number): void {
  ctx.beginPath();
  for (let i = 0; i < points * 2; i++) {
    const r = i % 2 === 0 ? outer : inner;
    const a = (Math.PI * i) / points - Math.PI / 2;
    const px = cx + Math.cos(a) * r;
    const py = cy + Math.sin(a) * r;
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  }
  ctx.closePath();
}

function drawBackground(ctx: Ctx, tier: Tier): void {
  // navy outline body
  ctx.beginPath();
  ctx.roundRect(0, 0, CARD_W, CARD_H, 30);
  ctx.fillStyle = NAVY;
  ctx.fill();
  // inner gradient
  ctx.save();
  ctx.beginPath();
  ctx.roundRect(7, 7, CARD_W - 14, CARD_H - 14, 24);
  ctx.clip();
  const g = ctx.createLinearGradient(0, 0, 0, CARD_H);
  g.addColorStop(0, tier.top);
  g.addColorStop(1, tier.bottom);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, CARD_W, CARD_H);
  // diagonal sunburst stripes
  ctx.globalAlpha = 0.13;
  ctx.fillStyle = '#ffffff';
  const cx = CARD_W / 2;
  const cy = 170;
  for (let i = 0; i < 18; i++) {
    const a0 = (i / 18) * Math.PI * 2;
    const a1 = a0 + Math.PI / 18;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.cos(a0) * 700, cy + Math.sin(a0) * 700);
    ctx.lineTo(cx + Math.cos(a1) * 700, cy + Math.sin(a1) * 700);
    ctx.closePath();
    ctx.fill();
  }
  // glossy top highlight
  ctx.globalAlpha = 0.25;
  ctx.beginPath();
  ctx.ellipse(CARD_W / 2, -40, CARD_W * 0.7, 110, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawPlaceholder(ctx: Ctx, x: number, y: number, w: number, h: number): void {
  ctx.fillStyle = '#4fb3ff';
  ctx.fillRect(x, y, w, h);
  ctx.save();
  ctx.beginPath();
  ctx.rect(x, y, w, h);
  ctx.clip();
  ctx.fillStyle = '#3aa4f5';
  for (let i = -h; i < w; i += 28) {
    ctx.beginPath();
    ctx.moveTo(x + i, y + h);
    ctx.lineTo(x + i + 14, y + h);
    ctx.lineTo(x + i + 14 + h, y);
    ctx.lineTo(x + i + h, y);
    ctx.closePath();
    ctx.fill();
  }
  // silhouette
  const cx = x + w / 2;
  ctx.fillStyle = NAVY;
  ctx.globalAlpha = 0.85;
  ctx.beginPath();
  ctx.arc(cx, y + h * 0.42, h * 0.2, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.ellipse(cx, y + h * 1.02, w * 0.3, h * 0.36, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalAlpha = 1;
  ctx.font = `64px ${DISPLAY}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  outlinedText(ctx, '?', cx, y + h * 0.44, YELLOW, 8);
  ctx.font = `20px ${DISPLAY}`;
  outlinedText(ctx, 'NO SELFIE', cx, y + h - 22, '#ffffff', 6);
  ctx.restore();
}

function drawPhoto(ctx: Ctx, img: HTMLImageElement | null, rank: number): void {
  const fw = 300;
  const fh = 236;
  const cx = CARD_W / 2;
  const cy = 168;
  const tilt = (rank % 2 === 0 ? 2.2 : -2.2) * (Math.PI / 180);
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(tilt);
  // drop shadow
  ctx.fillStyle = 'rgba(27,27,47,0.35)';
  ctx.fillRect(-fw / 2 + 6, -fh / 2 + 8, fw, fh);
  // polaroid frame
  ctx.fillStyle = '#ffffff';
  ctx.strokeStyle = NAVY;
  ctx.lineWidth = 5;
  ctx.beginPath();
  ctx.rect(-fw / 2, -fh / 2, fw, fh);
  ctx.fill();
  ctx.stroke();
  const px = -fw / 2 + 11;
  const py = -fh / 2 + 11;
  const pw = fw - 22;
  const ph = fh - 22;
  if (img) {
    const s = Math.max(pw / img.naturalWidth, ph / img.naturalHeight);
    const sw = pw / s;
    const sh = ph / s;
    ctx.save();
    ctx.beginPath();
    ctx.rect(px, py, pw, ph);
    ctx.clip();
    ctx.drawImage(img, (img.naturalWidth - sw) / 2, (img.naturalHeight - sh) / 2, sw, sh, px, py, pw, ph);
    // comic vignette
    const v = ctx.createRadialGradient(0, 0, ph * 0.35, 0, 0, pw * 0.75);
    v.addColorStop(0, 'rgba(0,0,0,0)');
    v.addColorStop(1, 'rgba(27,27,47,0.35)');
    ctx.fillStyle = v;
    ctx.fillRect(px, py, pw, ph);
    ctx.restore();
  } else {
    drawPlaceholder(ctx, px, py, pw, ph);
  }
  ctx.strokeStyle = NAVY;
  ctx.lineWidth = 3;
  ctx.strokeRect(px, py, pw, ph);
  // tape strips
  ctx.fillStyle = 'rgba(255,210,63,0.85)';
  ctx.strokeStyle = 'rgba(27,27,47,0.35)';
  ctx.lineWidth = 1.5;
  for (const [tx, ty, ta] of [
    [fw / 2 - 26, -fh / 2 + 4, 0.7],
    [-fw / 2 + 120, -fh / 2 - 4, -0.12],
  ] as const) {
    ctx.save();
    ctx.translate(tx, ty);
    ctx.rotate(ta);
    ctx.fillRect(-30, -10, 60, 20);
    ctx.strokeRect(-30, -10, 60, 20);
    ctx.restore();
  }
  ctx.restore();

  if (img) {
    // "CRASH!" burst sticker
    ctx.save();
    ctx.translate(CARD_W - 62, 262);
    ctx.rotate(0.2);
    star(ctx, 0, 0, 11, 46, 32);
    ctx.fillStyle = RED;
    ctx.strokeStyle = NAVY;
    ctx.lineWidth = 5;
    ctx.lineJoin = 'round';
    ctx.fill();
    ctx.stroke();
    star(ctx, 0, 0, 11, 34, 26);
    ctx.fillStyle = YELLOW;
    ctx.fill();
    ctx.font = `21px ${DISPLAY}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    outlinedText(ctx, 'CRASH!', 0, 1, '#ffffff', 5);
    ctx.restore();
  }
}

function drawMedal(ctx: Ctx, rank: number, tier: Tier): void {
  const cx = 56;
  const cy = 60;
  const r = 38;
  ctx.lineJoin = 'round';
  // ribbon tails
  ctx.strokeStyle = NAVY;
  ctx.lineWidth = 4;
  for (const [dx, col] of [
    [-14, RED],
    [14, BLUE],
  ] as const) {
    ctx.beginPath();
    ctx.moveTo(cx + dx - 11, cy + 10);
    ctx.lineTo(cx + dx + 11, cy + 10);
    ctx.lineTo(cx + dx + 11 + dx * 0.3, cy + r + 26);
    ctx.lineTo(cx + dx + dx * 0.3, cy + r + 18);
    ctx.lineTo(cx + dx - 11 + dx * 0.3, cy + r + 26);
    ctx.closePath();
    ctx.fillStyle = col;
    ctx.fill();
    ctx.stroke();
  }
  // disc
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  const g = ctx.createRadialGradient(cx - 12, cy - 14, 4, cx, cy, r);
  g.addColorStop(0, tier.medalHi);
  g.addColorStop(1, tier.medal);
  ctx.fillStyle = g;
  ctx.fill();
  ctx.lineWidth = 6;
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(cx, cy, r - 9, 0, Math.PI * 2);
  ctx.lineWidth = 2;
  ctx.strokeStyle = 'rgba(27,27,47,0.35)';
  ctx.stroke();
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  const label = `#${rank}`;
  fitFont(ctx, label, 56, 36, DISPLAY);
  ctx.fillStyle = NAVY;
  ctx.fillText(label, cx, cy + 3);
  if (rank === 1) {
    // crown
    ctx.save();
    ctx.translate(cx, cy - r - 4);
    ctx.rotate(-0.18);
    ctx.beginPath();
    ctx.moveTo(-24, 6);
    ctx.lineTo(-28, -18);
    ctx.lineTo(-12, -6);
    ctx.lineTo(0, -24);
    ctx.lineTo(12, -6);
    ctx.lineTo(28, -18);
    ctx.lineTo(24, 6);
    ctx.closePath();
    ctx.fillStyle = YELLOW;
    ctx.strokeStyle = NAVY;
    ctx.lineWidth = 4;
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = RED;
    ctx.beginPath();
    ctx.arc(0, -4, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }
}

function drawNameRibbon(ctx: Ctx, name: string, tier: Tier): void {
  const w = 316;
  const h = 46;
  const x = (CARD_W - w) / 2;
  const y = 300;
  const notch = 14;
  ctx.beginPath();
  ctx.moveTo(x - 8, y);
  ctx.lineTo(x + w + 8, y);
  ctx.lineTo(x + w + 8 - notch, y + h / 2);
  ctx.lineTo(x + w + 8, y + h);
  ctx.lineTo(x - 8, y + h);
  ctx.lineTo(x - 8 + notch, y + h / 2);
  ctx.closePath();
  ctx.fillStyle = tier.ribbon;
  ctx.strokeStyle = NAVY;
  ctx.lineWidth = 5;
  ctx.lineJoin = 'round';
  ctx.fill();
  ctx.stroke();
  const text = name.toUpperCase();
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  fitFont(ctx, text, w - 40, 32, DISPLAY);
  outlinedText(ctx, text, CARD_W / 2, y + h / 2 + 2, '#ffffff', 6);
}

function drawPill(ctx: Ctx, x: number, y: number, w: number, label: string, icon: 'coin' | 'run'): void {
  const h = 36;
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, h / 2);
  ctx.fillStyle = '#ffffff';
  ctx.strokeStyle = NAVY;
  ctx.lineWidth = 4;
  ctx.fill();
  ctx.stroke();
  const ix = x + 20;
  const iy = y + h / 2;
  if (icon === 'coin') {
    ctx.beginPath();
    ctx.arc(ix, iy, 11, 0, Math.PI * 2);
    ctx.fillStyle = YELLOW;
    ctx.fill();
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(ix, iy, 5, 0, Math.PI * 2);
    ctx.lineWidth = 2;
    ctx.strokeStyle = '#e0a800';
    ctx.stroke();
  } else {
    // track / distance flag
    ctx.fillStyle = GREEN;
    ctx.strokeStyle = NAVY;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(ix - 7, iy + 11);
    ctx.lineTo(ix - 7, iy - 11);
    ctx.lineTo(ix + 10, iy - 5);
    ctx.lineTo(ix - 7, iy + 1);
    ctx.stroke();
    ctx.fill();
  }
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = NAVY;
  fitFont(ctx, label, w - 46, 20, BODY, '900');
  ctx.fillText(label, x + 38, iy + 1);
}

/**
 * Render one Hall of Fame trading card (crash selfie, rank medal, name, score,
 * coins, distance, relative date) to a PNG data URL.
 */
export async function renderSelfieCard(entry: LeaderboardEntry, rank: number, now: number = Date.now()): Promise<string> {
  await ensureFonts();
  const img = entry.photo ? await loadImage(entry.photo) : null;
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(CARD_W * CARD_SCALE);
  canvas.height = Math.round(CARD_H * CARD_SCALE);
  const ctx = canvas.getContext('2d');
  if (!ctx) return '';
  ctx.scale(CARD_SCALE, CARD_SCALE);
  const tier = tierFor(rank);

  drawBackground(ctx, tier);
  drawPhoto(ctx, img, rank);
  drawMedal(ctx, rank, tier);
  drawNameRibbon(ctx, entry.name, tier);

  // score
  ctx.textAlign = 'center';
  ctx.textBaseline = 'alphabetic';
  ctx.font = `900 15px ${BODY}`;
  ctx.fillStyle = NAVY;
  ctx.fillText('S C O R E', CARD_W / 2, 370);
  const score = formatNum(entry.score);
  fitFont(ctx, score, CARD_W - 40, 60, DISPLAY);
  outlinedText(ctx, score, CARD_W / 2, 424, '#ffffff', 10);

  drawPill(ctx, 20, 436, 152, formatNum(entry.coins), 'coin');
  drawPill(ctx, CARD_W - 172, 436, 152, `${formatNum(entry.distance)} m`, 'run');

  // footer
  ctx.textBaseline = 'alphabetic';
  ctx.font = `800 14px ${BODY}`;
  ctx.fillStyle = 'rgba(27,27,47,0.8)';
  ctx.textAlign = entry.mode === 2 ? 'left' : 'center';
  ctx.fillText(relativeDate(entry.date, now), entry.mode === 2 ? 26 : CARD_W / 2, 501);
  if (entry.mode === 2) {
    ctx.beginPath();
    ctx.roundRect(CARD_W - 104, 485, 80, 22, 11);
    ctx.fillStyle = NAVY;
    ctx.fill();
    ctx.font = `14px ${DISPLAY}`;
    ctx.textAlign = 'center';
    ctx.fillStyle = YELLOW;
    ctx.fillText('2P RACE', CARD_W - 64, 501);
  }
  return canvas.toDataURL('image/png');
}
