// Procedural CanvasTexture painters (run once at init). All deterministic.
import {
  CanvasTexture,
  DataTexture,
  LinearMipmapLinearFilter,
  NearestFilter,
  RedFormat,
  RepeatWrapping,
  SRGBColorSpace,
} from 'three';
import { HashRng } from '../util/rand';
import { CAR, LIV, SIDE_LAYOUT, SWATCH_CELLS, frontPx, leftRow, rightRow } from './trainSpec';
import type { LiverySwatch } from './trainSpec';
import { LAYOUT, LIVERIES, SEGMENT_LENGTH } from '../config';

function canvas(w: number, h: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  const ctx = c.getContext('2d');
  if (!ctx) throw new Error('2D canvas unavailable');
  return [c, ctx];
}

function tex(c: HTMLCanvasElement, repeat = false): CanvasTexture {
  const t = new CanvasTexture(c);
  t.colorSpace = SRGBColorSpace;
  t.minFilter = LinearMipmapLinearFilter;
  t.generateMipmaps = true;
  if (repeat) {
    t.wrapS = RepeatWrapping;
    t.wrapT = RepeatWrapping;
  }
  t.needsUpdate = true;
  return t;
}

/** 3-step toon ramp. */
export function makeToonRamp(): DataTexture {
  const data = new Uint8Array([110, 190, 255]);
  const t = new DataTexture(data, 3, 1, RedFormat);
  t.minFilter = NearestFilter;
  t.magFilter = NearestFilter;
  t.generateMipmaps = false;
  t.needsUpdate = true;
  return t;
}

// ---------------------------------------------------------------------------
/** Gravel tile: 4 m × 4 m. */
export const GRAVEL_TILE = 4;
export function makeGravel(): CanvasTexture {
  const S = 512;
  const [c, g] = canvas(S, S);
  g.fillStyle = '#9c8a76';
  g.fillRect(0, 0, S, S);
  const r = new HashRng().seed(11);
  for (let i = 0; i < 5200; i++) {
    const x = r.next() * S;
    const y = r.next() * S;
    const s = r.range(1.5, 5);
    const l = r.range(42, 72);
    const hue = r.range(20, 40);
    g.fillStyle = `hsl(${hue},${r.range(8, 22)}%,${l}%)`;
    g.beginPath();
    g.ellipse(x, y, s, s * r.range(0.6, 1), r.next() * 3, 0, Math.PI * 2);
    g.fill();
    // wrap copies so the tile repeats seamlessly
    if (x < 6 || y < 6) {
      g.beginPath();
      g.ellipse(x < 6 ? x + S : x, y < 6 ? y + S : y, s, s, 0, 0, Math.PI * 2);
      g.fill();
    }
  }
  return tex(c, true);
}

// ---------------------------------------------------------------------------
/** Building facade tile: one 3 m × 3 m floor module, white walls (tinted by vertex color). */
export const FACADE_TILE = 3;
export function makeFacade(): CanvasTexture {
  const S = 256;
  const [c, g] = canvas(S, S);
  g.fillStyle = '#ffffff';
  g.fillRect(0, 0, S, S);
  // floor band
  g.fillStyle = '#e6e2dc';
  g.fillRect(0, S - 18, S, 18);
  // window
  const wx = 64, wy = 52, ww = 128, wh = 136;
  g.fillStyle = '#d8d4cc';
  g.fillRect(wx - 8, wy - 8, ww + 16, wh + 22);
  const grd = g.createLinearGradient(0, wy, 0, wy + wh);
  grd.addColorStop(0, '#5f86b8');
  grd.addColorStop(1, '#2f4f80');
  g.fillStyle = grd;
  g.fillRect(wx, wy, ww, wh);
  g.fillStyle = 'rgba(255,255,255,0.35)';
  g.beginPath();
  g.moveTo(wx + 14, wy + wh);
  g.lineTo(wx + 60, wy);
  g.lineTo(wx + 86, wy);
  g.lineTo(wx + 40, wy + wh);
  g.fill();
  g.fillStyle = '#f2efe9';
  g.fillRect(wx + ww / 2 - 4, wy, 8, wh);
  g.fillRect(wx - 12, wy + wh + 6, ww + 24, 10);
  return tex(c, true);
}
/** A uv inside the facade tile that is plain wall (for roofs / trims). */
export const FACADE_PLAIN: readonly [number, number] = [0.06, 0.5];

// ---------------------------------------------------------------------------
/** Graffiti wall atlas: 4 bands, each one 40 m wall run (u = along, v band). */
export const WALL_BANDS = 4;
const WORDS = ['BODY', 'SURF', 'WOW', 'JUMP', 'ZAP!', 'YO', 'RUN', 'COOL', 'BOOM', 'HEY', 'GO!', 'FRESH', 'DUCK', 'POW'];
const GRAF = [
  ['#ff3d7f', '#ffd23f'], ['#3df2ff', '#2a6bff'], ['#7dff4d', '#13b05a'], ['#ff8a1a', '#ff2a2a'],
  ['#c35bff', '#ff5bd8'], ['#ffe14d', '#ff7a1a'], ['#4dfff0', '#7b3fe4'],
];
export function makeGraffiti(): CanvasTexture {
  const W = 2048, BH = 256, H = BH * WALL_BANDS;
  const [c, g] = canvas(W, H);
  const pxm = W / SEGMENT_LENGTH; // horizontal px per meter
  const vScale = BH / (LAYOUT.wallHeight * pxm);
  const r = new HashRng();
  for (let b = 0; b < WALL_BANDS; b++) {
    r.seed(97, b);
    g.save();
    g.translate(0, b * BH);
    g.scale(1, vScale);
    const hh = LAYOUT.wallHeight * pxm; // band height in local units
    // concrete
    const base = g.createLinearGradient(0, 0, 0, hh);
    base.addColorStop(0, '#d9d3c8');
    base.addColorStop(0.85, '#c4bdb1');
    base.addColorStop(1, '#9d968a');
    g.fillStyle = base;
    g.fillRect(0, 0, W, hh);
    for (let x = 0; x < W; x += pxm * 4) {
      g.fillStyle = 'rgba(80,70,60,0.25)';
      g.fillRect(x, 0, 3, hh);
      g.fillStyle = 'rgba(255,255,255,0.25)';
      g.fillRect(x + 3, 0, 2, hh);
    }
    g.fillStyle = 'rgba(255,255,255,0.35)';
    g.fillRect(0, 0, W, 6);
    for (let i = 0; i < 26; i++) {
      g.fillStyle = `rgba(90,80,70,${r.range(0.04, 0.12)})`;
      g.beginPath();
      g.ellipse(r.next() * W, hh * r.range(0.5, 1), r.range(20, 80), r.range(10, 40), 0, 0, Math.PI * 2);
      g.fill();
    }
    // pieces
    let x = r.range(10, 80);
    while (x < W - 200) {
      const kind = r.next();
      if (kind < 0.62) {
        const word = r.pick(WORDS);
        const size = r.range(70, 110);
        x = bubbleWord(g, r, word, x, hh * r.range(0.62, 0.78), size);
      } else if (kind < 0.82) {
        x = tag(g, r, x, hh * r.range(0.4, 0.75));
      } else {
        x = blobArt(g, r, x, hh * r.range(0.35, 0.6));
      }
      x += r.range(30, 140);
    }
    g.restore();
  }
  const t = tex(c);
  t.anisotropy = 4;
  return t;
}

function bubbleWord(g: CanvasRenderingContext2D, r: HashRng, word: string, x: number, base: number, size: number): number {
  const [c1, c2] = r.pick(GRAF);
  g.font = `900 ${size}px "Arial Black", "Lilita One", Impact, sans-serif`;
  g.lineJoin = 'round';
  g.textBaseline = 'alphabetic';
  const w = g.measureText(word).width;
  // spray cloud behind
  const cloud = g.createRadialGradient(x + w / 2, base - size * 0.35, 4, x + w / 2, base - size * 0.35, w * 0.7);
  cloud.addColorStop(0, hexA(c2, 0.45));
  cloud.addColorStop(1, hexA(c2, 0));
  g.fillStyle = cloud;
  g.fillRect(x - 40, base - size * 1.4, w + 80, size * 1.8);
  g.save();
  g.translate(x, base);
  g.rotate(r.range(-0.08, 0.08));
  g.strokeStyle = '#151020';
  g.lineWidth = size * 0.28;
  g.strokeText(word, 0, 0);
  g.strokeStyle = '#ffffff';
  g.lineWidth = size * 0.12;
  g.strokeText(word, 0, 0);
  const grd = g.createLinearGradient(0, -size * 0.8, 0, 0);
  grd.addColorStop(0, c1);
  grd.addColorStop(1, c2);
  g.fillStyle = grd;
  g.fillText(word, 0, 0);
  // highlights
  g.fillStyle = 'rgba(255,255,255,0.85)';
  for (let i = 0; i < word.length; i++) {
    g.beginPath();
    g.ellipse((w / word.length) * (i + 0.3), -size * 0.6, size * 0.06, size * 0.035, -0.5, 0, Math.PI * 2);
    g.fill();
  }
  // drips
  g.fillStyle = c2;
  for (let i = 0; i < 3; i++) {
    const dx = r.range(0.1, 0.9) * w;
    const dl = r.range(10, 40);
    g.fillRect(dx, -4, 4, dl);
    g.beginPath();
    g.arc(dx + 2, dl - 4, 3.5, 0, Math.PI * 2);
    g.fill();
  }
  g.restore();
  return x + w;
}

function tag(g: CanvasRenderingContext2D, r: HashRng, x: number, base: number): number {
  const names = ['zk8', 'Rizo', 'SKY', 'mojo', 'Kass', 'dr3w', 'NEO', 'Lu'];
  const size = r.range(34, 56);
  g.font = `italic 700 ${size}px "Brush Script MT", "Comic Sans MS", cursive`;
  const s = r.pick(names);
  g.fillStyle = r.chance(0.5) ? '#1a1430' : r.pick(GRAF)[0];
  g.save();
  g.translate(x, base);
  g.rotate(r.range(-0.2, 0.05));
  g.fillText(s, 0, 0);
  g.lineWidth = 3;
  g.strokeStyle = g.fillStyle;
  g.beginPath();
  const w = g.measureText(s).width;
  g.moveTo(-6, 8);
  g.quadraticCurveTo(w / 2, 22, w + 10, 2);
  g.stroke();
  g.restore();
  return x + w;
}

function blobArt(g: CanvasRenderingContext2D, r: HashRng, x: number, cy: number): number {
  const rad = r.range(34, 52);
  const [c1, c2] = r.pick(GRAF);
  // smiley / star blob
  g.fillStyle = '#151020';
  g.beginPath();
  g.arc(x + rad, cy, rad + 7, 0, Math.PI * 2);
  g.fill();
  const grd = g.createRadialGradient(x + rad * 0.7, cy - rad * 0.3, 3, x + rad, cy, rad);
  grd.addColorStop(0, c1);
  grd.addColorStop(1, c2);
  g.fillStyle = grd;
  g.beginPath();
  g.arc(x + rad, cy, rad, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = '#151020';
  g.beginPath();
  g.ellipse(x + rad * 0.65, cy - rad * 0.2, rad * 0.12, rad * 0.2, 0, 0, Math.PI * 2);
  g.ellipse(x + rad * 1.35, cy - rad * 0.2, rad * 0.12, rad * 0.2, 0, 0, Math.PI * 2);
  g.fill();
  g.lineWidth = rad * 0.12;
  g.strokeStyle = '#151020';
  g.beginPath();
  g.arc(x + rad, cy + rad * 0.05, rad * 0.55, 0.2, Math.PI - 0.2);
  g.stroke();
  return x + rad * 2;
}

function hexA(hex: string, a: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

// ---------------------------------------------------------------------------
/** Props atlas 512²: stripe panels, chevrons, coin face, badges. Pixel rects. */
export const PROPS = {
  size: 512,
  redStripes: [0, 0, 256, 128],
  yellowStripes: [0, 128, 256, 128],
  chevrons: [256, 0, 256, 256],
  white: [0, 256, 128, 128],
  badge2x: [128, 256, 128, 128],
  coinFace: [256, 256, 128, 128],
  board: [384, 256, 128, 128],
  sign: [0, 384, 256, 128],
} as const;

export function makeProps(): CanvasTexture {
  const S = PROPS.size;
  const [c, g] = canvas(S, S);
  const stripes = (rx: number, ry: number, rw: number, rh: number, a: string, b: string): void => {
    g.save();
    g.beginPath();
    g.rect(rx, ry, rw, rh);
    g.clip();
    g.fillStyle = a;
    g.fillRect(rx, ry, rw, rh);
    g.fillStyle = b;
    for (let i = -rh; i < rw + rh; i += 64) {
      g.beginPath();
      g.moveTo(rx + i, ry + rh);
      g.lineTo(rx + i + 32, ry + rh);
      g.lineTo(rx + i + 32 + rh, ry);
      g.lineTo(rx + i + rh, ry);
      g.fill();
    }
    g.restore();
  };
  stripes(0, 0, 256, 128, '#ffffff', '#e8323e');
  stripes(0, 128, 256, 128, '#ffcc1a', '#1c1a22');
  // chevrons (pointing toward v=1 = far end)
  g.fillStyle = '#ffc928';
  g.fillRect(256, 0, 256, 256);
  g.fillStyle = '#1c1a22';
  for (let i = 0; i < 4; i++) {
    const y = 20 + i * 62;
    g.beginPath();
    g.moveTo(256 + 30, y + 40);
    g.lineTo(256 + 128, y);
    g.lineTo(256 + 226, y + 40);
    g.lineTo(256 + 226, y + 60);
    g.lineTo(256 + 128, y + 22);
    g.lineTo(256 + 30, y + 60);
    g.fill();
  }
  g.fillStyle = '#e09a12';
  g.fillRect(256, 0, 12, 256);
  g.fillRect(500, 0, 12, 256);
  g.fillStyle = '#ffffff';
  g.fillRect(0, 256, 128, 128);
  // 2x badge
  g.fillStyle = '#b45cff';
  g.fillRect(128, 256, 128, 128);
  g.fillStyle = '#ffffff';
  g.beginPath();
  g.arc(192, 320, 56, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = '#7b2fe0';
  g.beginPath();
  g.arc(192, 320, 48, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = '#ffffff';
  g.font = '900 54px "Arial Black", Impact, sans-serif';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.fillText('2×', 192, 324);
  // coin face
  g.fillStyle = '#ffc62e';
  g.fillRect(256, 256, 128, 128);
  g.fillStyle = '#ffe680';
  g.beginPath();
  g.arc(320, 320, 50, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = '#f0a818';
  star(g, 320, 320, 34, 15);
  g.fillStyle = '#fff6b8';
  star(g, 316, 316, 26, 11);
  // hoverboard deck
  const bg = g.createLinearGradient(384, 256, 512, 384);
  bg.addColorStop(0, '#28e0ff');
  bg.addColorStop(1, '#2a6bff');
  g.fillStyle = bg;
  g.fillRect(384, 256, 128, 128);
  g.fillStyle = '#ffe14d';
  g.fillRect(384, 300, 128, 12);
  g.fillStyle = '#ff3d7f';
  g.fillRect(384, 328, 128, 12);
  // sign
  g.fillStyle = '#1f6fe0';
  g.fillRect(0, 384, 256, 128);
  g.fillStyle = '#ffffff';
  g.font = '900 60px "Arial Black", Impact, sans-serif';
  g.fillText('M', 128, 448);
  return tex(c);
}

function star(g: CanvasRenderingContext2D, cx: number, cy: number, ro: number, ri: number): void {
  g.beginPath();
  for (let i = 0; i < 10; i++) {
    const a = -Math.PI / 2 + (i * Math.PI) / 5;
    const rr = i % 2 === 0 ? ro : ri;
    if (i === 0) g.moveTo(cx + Math.cos(a) * rr, cy + Math.sin(a) * rr);
    else g.lineTo(cx + Math.cos(a) * rr, cy + Math.sin(a) * rr);
  }
  g.closePath();
  g.fill();
}

// ---------------------------------------------------------------------------
/** Train livery texture (layout in trainSpec). */
export function makeLivery(variant: number): CanvasTexture {
  const S = LIV.size;
  const [c, g] = canvas(S, S);
  const [body, accent, trim] = LIVERIES[variant % LIVERIES.length];
  const bodyDark = shade(body, -0.25);
  const pxPerM = S / CAR.bodyLength;
  // side wrap
  g.fillStyle = '#b9c0c9';
  g.fillRect(0, 0, S, LIV.sideRows); // roof base
  const band = (yLo: number, yHi: number, color: string): void => {
    g.fillStyle = color;
    const l0 = leftRow(yHi), l1 = leftRow(yLo);
    g.fillRect(0, l0, S, l1 - l0);
    const r0 = rightRow(yLo), r1 = rightRow(yHi);
    g.fillRect(0, r0, S, r1 - r0);
  };
  const rect = (z0: number, z1: number, yLo: number, yHi: number, color: string, inset = 0): void => {
    g.fillStyle = color;
    const x0 = z0 * pxPerM + inset, x1 = z1 * pxPerM - inset;
    const l0 = leftRow(yHi) + inset, l1 = leftRow(yLo) - inset;
    g.fillRect(x0, l0, x1 - x0, l1 - l0);
    const r0 = rightRow(yLo) + inset, r1 = rightRow(yHi) - inset;
    g.fillRect(x0, r0, x1 - x0, r1 - r0);
  };
  band(CAR.bodyBottom, CAR.sideTop + 0.01, body);
  band(CAR.bodyBottom, 0.85, bodyDark);
  band(1.0, 1.28, accent);
  band(1.28, 1.36, trim);
  band(CAR.sideTop - 0.12, CAR.sideTop + 0.01, trim);
  // roof gradient center
  const roofTop = leftRow(CAR.sideTop + 0.6);
  g.fillStyle = '#d2d8de';
  g.fillRect(0, roofTop, S, rightRow(CAR.sideTop + 0.6) - roofTop);
  for (const [a, b] of SIDE_LAYOUT.windows) {
    rect(a, b, 1.55, 2.45, '#20283a');
    rect(a, b, 1.55, 2.45, '#5a7fae', 6);
    rect(a + 0.1, a + 0.35, 1.7, 2.35, 'rgba(255,255,255,0.35)', 0);
  }
  for (const [a, b] of SIDE_LAYOUT.doors) {
    rect(a, b, 0.62, 2.62, '#20283a');
    rect(a, b, 0.62, 2.62, shade(body, 0.12), 5);
    const m = (a + b) / 2;
    rect(m - 0.03, m + 0.03, 0.62, 2.62, '#20283a');
    rect(a + 0.12, m - 0.1, 1.6, 2.4, '#5a7fae');
    rect(m + 0.1, b - 0.12, 1.6, 2.4, '#5a7fae');
  }
  // front
  const F = LIV.front;
  g.fillStyle = body;
  g.fillRect(F.x, F.y, F.w, F.h);
  const fr = (x0: number, y0: number, x1: number, y1: number, color: string): void => {
    const [ax, ay] = frontPx(x0, y1);
    const [bx, by] = frontPx(x1, y0);
    g.fillStyle = color;
    g.fillRect(ax, ay, bx - ax, by - ay);
  };
  const hw = CAR.halfWidth;
  fr(-hw, CAR.bodyBottom, hw, 0.85, bodyDark);
  fr(-hw, 1.0, hw, 1.28, accent);
  fr(-hw + 0.22, 1.65, hw - 0.22, 2.6, '#1a2234');
  fr(-hw + 0.3, 1.72, hw - 0.3, 2.52, '#4f74a6');
  fr(-hw + 0.4, 1.8, -hw + 0.75, 2.45, 'rgba(255,255,255,0.35)');
  fr(-0.03, 1.65, 0.03, 2.6, '#1a2234');
  fr(-hw, 2.85, hw, 3.2, '#c9d0d8');
  // headlights
  for (const sx of [-1, 1]) {
    const [cx, cy] = frontPx(sx * (hw - 0.42), 1.45);
    g.fillStyle = '#20283a';
    g.beginPath();
    g.arc(cx, cy, 30, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = '#fff6c8';
    g.beginPath();
    g.arc(cx, cy, 22, 0, Math.PI * 2);
    g.fill();
  }
  // number plate
  fr(-0.35, 1.35, 0.35, 1.55, '#20283a');
  g.fillStyle = '#ffd23f';
  g.font = '900 34px "Arial Black", Impact, sans-serif';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  const [px, py] = frontPx(0, 1.45);
  g.fillText(String(100 + variant * 37), px, py + 2);
  // swatches
  const sw: Record<LiverySwatch, string> = {
    white: '#ffffff', black: '#1a1a20', under: '#3a3f48', chrome: '#c9d0d8',
    glass: '#4f74a6', roof: '#d2d8de', body, accent,
    light: '#fff6c8', red: '#e8323e', bodyDark, trim,
    rubber: '#2a2a30', yellow: '#ffd23f',
  };
  for (const k of Object.keys(SWATCH_CELLS) as LiverySwatch[]) {
    const [cx, cy] = SWATCH_CELLS[k];
    g.fillStyle = sw[k];
    g.fillRect(LIV.swatchOrigin.x + cx * LIV.swatchSize, LIV.swatchOrigin.y + cy * LIV.swatchSize, LIV.swatchSize, LIV.swatchSize);
  }
  const t = tex(c);
  t.anisotropy = 4;
  return t;
}

export function shade(hex: string, amt: number): string {
  const n = parseInt(hex.slice(1), 16);
  const f = (v: number): number => Math.max(0, Math.min(255, Math.round(amt < 0 ? v * (1 + amt) : v + (255 - v) * amt)));
  const rr = f((n >> 16) & 255), gg = f((n >> 8) & 255), bb = f(n & 255);
  return `#${((1 << 24) | (rr << 16) | (gg << 8) | bb).toString(16).slice(1)}`;
}

/** Soft round sprite for particles / glows. */
export function makeGlowSprite(): CanvasTexture {
  const S = 64;
  const [c, g] = canvas(S, S);
  const grd = g.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, S / 2);
  grd.addColorStop(0, 'rgba(255,255,255,1)');
  grd.addColorStop(0.35, 'rgba(255,255,255,0.75)');
  grd.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grd;
  g.fillRect(0, 0, S, S);
  const t = new CanvasTexture(c);
  t.needsUpdate = true;
  return t;
}
