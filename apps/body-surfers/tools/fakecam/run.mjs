#!/usr/bin/env node
// Open a page in headless system Chrome with a synthetic webcam: getUserMedia/enumerateDevices
// are overridden so the page receives a live canvas stream of a scripted person (scene.js).
//
//   node tools/fakecam/run.mjs --routine solo --url "http://localhost:5173/?autostart=1" \
//        [--seconds 22] [--shots 2000,9000,15000] [--out shots/fakecam.png] [--fps 30]
//        [--size 1280x720] [--loop] [--start-delay 0] [--assets http://localhost:5173]
//        [--eval "<js expr>"] [--logs] [--width 1280 --height 720]
//
// Shot times are ms after page load. The routine clock starts at the page's first
// getUserMedia({video}) call (+ --start-delay ms). Phase changes are printed as they happen.
// --assets: origin of a Vite dev server for this repo that serves /tools/fakecam/*.js
// (default: the page's origin).
import { chromium } from 'playwright-core';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

const args = process.argv.slice(2);
const opt = (n, d) => { const i = args.indexOf(`--${n}`); return i >= 0 && i + 1 < args.length && !args[i + 1].startsWith('--') ? args[i + 1] : d; };
const has = (n) => args.includes(`--${n}`);
const url = opt('url');
if (!url) { console.error('missing --url'); process.exit(2); }
const routine = opt('routine', 'solo');
const ROUTINE_SECONDS = { solo: 18.8, duo: 19.8, spectator: 18.8, idle: 10 };
const seconds = Number(opt('seconds', (ROUTINE_SECONDS[routine] ?? 20) + 3));
const out = opt('out', `shots/fakecam-${routine}.png`);
const shots = opt('shots') ? opt('shots').split(',').map(Number) : [];
const [cw, ch] = (opt('size', '1280x720')).split('x').map(Number);
const cfg = { routine, fps: Number(opt('fps', 30)), width: cw, height: ch, loop: has('loop'), startDelayMs: Number(opt('start-delay', 0)) };
const assets = opt('assets', new URL(url).origin);

const init = ({ cfg, assets }) => {
  const md = navigator.mediaDevices;
  if (!md) return;
  const realGUM = md.getUserMedia?.bind(md);
  const realEnum = md.enumerateDevices?.bind(md);
  const fake = { deviceId: 'fakecam', kind: 'videoinput', label: `FakeCam (${cfg.routine})`, groupId: 'fakecam' };
  fake.toJSON = () => ({ ...fake });
  md.getUserMedia = async (c = {}) => {
    if (!c.video) return realGUM(c);
    const m = await import(`${assets}/tools/fakecam/fakecam.js`);
    return m.startFakeCam(cfg, c);
  };
  md.enumerateDevices = async () => {
    const real = realEnum ? (await realEnum()).filter((d) => d.kind !== 'videoinput') : [];
    return [Object.setPrototypeOf({ ...fake }, (window.InputDeviceInfo ?? Object).prototype), ...real].map((d) => (d.deviceId === 'fakecam' ? fake : d));
  };
  window.__fakecamInstalled = true;
};

let t0 = Date.now(), loaded = false;
const browser = await chromium.launch({
  channel: 'chrome', headless: true,
  args: ['--enable-webgl', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader', '--autoplay-policy=no-user-gesture-required', '--use-fake-ui-for-media-stream'],
});
const context = await browser.newContext({ viewport: { width: Number(opt('width', 1280)), height: Number(opt('height', 720)) }, deviceScaleFactor: 1 });
await context.grantPermissions(['camera', 'microphone'], { origin: new URL(url).origin }).catch(() => {});
await context.addInitScript(init, { cfg, assets });
// Vite HMR full-reloads the page whenever anyone edits an .html file (other agents share the
// dev server), which would restart the routine. Replace the HMR client with an inert stub
// that still injects CSS. Pass --hmr to keep the real client.
if (!has('hmr')) {
  await context.route(/\/@vite\/client(\?.*)?$/, (route) => route.fulfill({ contentType: 'text/javascript', body: `
    const noop = () => {};
    export function createHotContext() { return { data: {}, accept: noop, acceptExports: noop, dispose: noop, prune: noop, invalidate: noop, decline: noop, on: noop, off: noop, send: noop }; }
    export function updateStyle(id, css) {
      let s = document.querySelector('style[data-vite-dev-id="' + CSS.escape(id) + '"]');
      if (!s) { s = document.createElement('style'); s.setAttribute('type', 'text/css'); s.setAttribute('data-vite-dev-id', id); document.head.appendChild(s); }
      s.textContent = css;
    }
    export function removeStyle(id) { document.querySelector('style[data-vite-dev-id="' + CSS.escape(id) + '"]')?.remove(); }
    export function injectQuery(u) { return u; }
    export class ErrorOverlay extends HTMLElement {}
  ` }));
}
const page = await context.newPage();
page.on('framenavigated', (f) => { if (f === page.mainFrame() && loaded) console.log(`[fakecam] page navigated/reloaded: ${f.url()} (routine restarts)`); });
let pageErrors = 0;
page.on('console', (m) => { const t = m.type(); if (t === 'error' || t === 'warning' || has('logs')) console.log(`[console.${t}] ${m.text()}`); });
page.on('pageerror', (e) => { pageErrors++; console.log(`[pageerror] ${e.message}`); });

await page.goto(url, { waitUntil: 'load', timeout: 30000 });
t0 = Date.now(); loaded = true;
let lastPhases = 0, lastFps = 0;
const poll = setInterval(async () => {
  try {
    const s = await page.evaluate(() => window.__fakecam && { phases: window.__fakecam.phases, fps: window.__fakecam.fps });
    if (!s) return;
    if (s.phases.length < lastPhases) lastPhases = 0;
    for (const p of s.phases.slice(lastPhases)) console.log(`[fakecam] +${((Date.now() - t0) / 1000).toFixed(1)}s routine t=${p.t}s phase=${p.phase} (render ${s.fps} fps)`);
    lastPhases = s.phases.length; lastFps = s.fps;
  } catch { /* navigation */ }
}, 200);

for (const ms of [...shots].sort((a, b) => a - b)) {
  const wait = ms - (Date.now() - t0);
  if (wait > 0) await page.waitForTimeout(wait);
  const file = out.replace(/\.png$/, `-${ms}.png`);
  mkdirSync(dirname(file), { recursive: true });
  await page.screenshot({ path: file });
  console.log(`[shot] ${file}`);
}
const rest = seconds * 1000 - (Date.now() - t0);
if (rest > 0) await page.waitForTimeout(rest);
clearInterval(poll);
const st = await page.evaluate(() => ({ installed: !!window.__fakecamInstalled, cam: window.__fakecam ? { t: window.__fakecam.t, frames: window.__fakecam.frames, fps: window.__fakecam.fps } : null }));
if (!st.cam) console.log('[fakecam] page never requested a video stream (getUserMedia not called)');
else console.log(`[fakecam] rendered ${st.cam.frames} frames, routine t=${st.cam.t.toFixed(1)}s, last fps ${st.cam.fps}`);
const expr = opt('eval');
if (expr) {
  try { console.log(`[eval] ${await page.evaluate((e) => JSON.stringify((0, eval)(e), null, 2), expr)}`); }
  catch (e) { console.log(`[eval-error] ${e.message}`); }
}
await browser.close();
console.log(`[done] pageErrors=${pageErrors}`);
process.exit(pageErrors > 0 ? 1 : 0);
