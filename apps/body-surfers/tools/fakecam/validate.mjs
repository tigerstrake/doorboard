#!/usr/bin/env node
// Render a routine headlessly and run PoseLandmarker on every frame; print per-phase
// detection rates and save sample frames to test-media/.
//   node tools/fakecam/validate.mjs --routine solo [--model lite|full] [--fps 15] [--origin http://localhost:5173]
import { chromium } from 'playwright-core';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const args = process.argv.slice(2);
const opt = (n, d) => { const i = args.indexOf(`--${n}`); return i >= 0 ? args[i + 1] : d; };
const routine = opt('routine', 'solo'), model = opt('model', 'lite'), fps = opt('fps', '15');
const origin = opt('origin', 'http://localhost:5173');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const outDir = resolve(root, 'test-media');
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ channel: 'chrome', headless: true,
  args: ['--enable-webgl', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader'] });
const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
page.on('pageerror', (e) => console.log('[pageerror]', e.message));
page.on('console', (m) => { if (m.type() === 'error') console.log('[console.error]', m.text()); });
await page.goto(`${origin}/tools/fakecam/validate.html?routine=${routine}&model=${model}&fps=${fps}`);
await page.waitForFunction(() => window.__result, null, { timeout: 600000, polling: 500 });
const r = await page.evaluate(() => window.__result);
// sample frames
for (const t of (opt('frames', '1,5,10.5,12.3,15.8') ).split(',').map(Number)) {
  await page.evaluate((tt) => window.__world.renderAt(tt), t);
  const b64 = await page.evaluate(() => window.__world.canvas.toDataURL('image/png').split(',')[1]);
  writeFileSync(resolve(outDir, `${routine}-t${t}.png`), Buffer.from(b64, 'base64'));
}
await browser.close();
writeFileSync(resolve(outDir, `${routine}-${model}-validate.json`), JSON.stringify(r));

const nActors = r.frames[0].actors.length;
let allHit = 0, allN = 0;
for (let a = 0; a < nActors; a++) {
  const stats = new Map();
  for (const f of r.frames) {
    const x = f.actors[a]; const s = stats.get(x.phase) ?? { n: 0, hit: 0 };
    s.n++; if (x.hit) s.hit++; stats.set(x.phase, s);
    if (!x.spectator) { allN++; if (x.hit) allHit++; }
  }
  console.log(`actor ${a}${r.frames[0].actors[a].spectator ? ' (spectator)' : ''}:`);
  for (const [ph, s] of stats) console.log(`  ${ph.padEnd(14)} ${(100 * s.hit / s.n).toFixed(0).padStart(4)}%  (${s.hit}/${s.n})`);
}
console.log(`OVERALL players ${routine}/${model}: ${(100 * allHit / allN).toFixed(1)}% of ${allN} actor-frames; frames with extra (unmatched) poses: ${r.frames.filter((f) => f.n > f.actors.filter((x) => x.hit).length).length}`);
