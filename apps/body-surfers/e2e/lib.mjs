// Shared helpers for the BODY SURFERS e2e + perf runners.
// Plain Node + playwright-core driving the system Chrome (same setup as scripts/shot.mjs).
import { chromium } from 'playwright-core';
import { existsSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const SHOTS_DIR = join(ROOT, 'shots', 'e2e');

// Mirrors src/shared/constants.ts (kept literal so the runner needs no TS build).
export const STORAGE_KEYS = {
  settings: 'bodysurfers.settings.v1',
  leaderboard: 'bodysurfers.leaderboard.v1',
};
export const BROADCAST_CHANNEL = 'bodysurfers-booth';

export function argOpt(args, name, def) {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && i + 1 < args.length && !args[i + 1].startsWith('--') ? args[i + 1] : def;
}
export const argHas = (args, name) => args.includes(`--${name}`);

/** First reachable dev server: --base, else 5173, else 5187. */
export async function resolveBase(explicit) {
  const candidates = explicit ? [explicit] : ['http://localhost:5173', 'http://localhost:5187'];
  for (const c of candidates) {
    try {
      const r = await fetch(c + '/', { signal: AbortSignal.timeout(3000) });
      if (r.ok) return c.replace(/\/$/, '');
    } catch {
      /* try next */
    }
  }
  throw new Error(`no dev server reachable at ${candidates.join(', ')} (run "npm run dev")`);
}

/** Optional fake video file for Chrome's fake camera (y4m / mjpeg). E2E_FAKE_VIDEO or test-media/*. */
export function findFakeVideo() {
  if (process.env.E2E_FAKE_VIDEO) return resolve(process.env.E2E_FAKE_VIDEO);
  const dir = join(ROOT, 'test-media');
  if (!existsSync(dir)) return null;
  const f = readdirSync(dir).find((n) => /\.(y4m|mjpeg)$/i.test(n));
  return f ? join(dir, f) : null;
}

export async function launch({ camera = false, fakeVideo = null } = {}) {
  const args = [
    '--enable-webgl',
    '--ignore-gpu-blocklist',
    '--enable-unsafe-swiftshader',
    '--autoplay-policy=no-user-gesture-required',
    // keep rAF running at full speed in headless / background
    '--disable-background-timer-throttling',
    '--disable-renderer-backgrounding',
    '--disable-backgrounding-occluded-windows',
  ];
  if (camera) {
    args.push('--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream');
    if (fakeVideo) args.push(`--use-file-for-fake-video-capture=${fakeVideo}`);
  }
  return chromium.launch({ channel: 'chrome', headless: true, args });
}

// Console noise that is not an app error (MediaPipe/TFLite wasm logging, GPU chatter).
const BENIGN = [/^INFO:/, /XNNPACK/i, /^W\d{4}/, /^I\d{4}/, /gl_context/i, /GPU stall/i, /GroupMarkerNotSet/i, /favicon/i];

/** New context + page with error capture. Returns a ctx object used by scenarios. */
export async function openPage(browser, base, { camera = false, width = 1280, height = 720 } = {}) {
  const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1 });
  if (camera) await context.grantPermissions(['camera'], { origin: new URL(base).origin });
  const logs = [];
  const errors = [];
  const attach = (page, tag) => {
    page.on('console', (msg) => {
      const type = msg.type();
      const text = msg.text();
      logs.push(`[${tag} console.${type}] ${text}`);
      // "Failed to load resource" is reported with its URL by the response listener
      if (type === 'error' && !BENIGN.some((r) => r.test(text)) && !/^Failed to load resource/.test(text)) errors.push(`[${tag} console.error] ${text}`);
    });
    page.on('pageerror', (err) => {
      logs.push(`[${tag} pageerror] ${err.message}\n${err.stack ?? ''}`);
      errors.push(`[${tag} pageerror] ${err.message}`);
    });
    page.on('response', (res) => {
      if (res.status() >= 400 && !/favicon/.test(res.url())) {
        const t = `[${tag} http ${res.status()}] ${res.url()}`;
        logs.push(t);
        errors.push(t);
      }
    });
    page.on('requestfailed', (req) => {
      const t = `[${tag} requestfailed] ${req.url()} ${req.failure()?.errorText ?? ''}`;
      logs.push(t);
      if (!/favicon|net::ERR_ABORTED/.test(t)) errors.push(t);
    });
  };
  const page = await context.newPage();
  attach(page, 'page');
  // Vite full reloads (other agents saving files) restart the app mid-scenario; count them so
  // failures can be flagged as possibly caused by a concurrent edit rather than the app.
  let navs = 0;
  page.on('framenavigated', (f) => {
    if (f === page.mainFrame()) navs++;
  });
  const ctx = {
    base,
    context,
    page,
    logs,
    errors,
    pages: [page],
    gotos: 0,
    get unexpectedReloads() {
      return Math.max(0, navs - ctx.gotos);
    },
    markReload() {
      ctx.gotos++;
    },
    async newPage(tag = `page${ctx.pages.length}`) {
      const p = await context.newPage();
      attach(p, tag);
      ctx.pages.push(p);
      return p;
    },
    url: (path) => base + path,
    async goto(path, p = page) {
      if (p === page) ctx.gotos++;
      await p.goto(base + path, { waitUntil: 'load', timeout: 30000 });
    },
    async reload() {
      ctx.gotos++;
      await page.reload({ waitUntil: 'load', timeout: 30000 });
    },
    state: (p = page) => getState(p),
    waitState: (pred, opts = {}) => waitState(opts.page ?? page, pred, opts),
    press: (key, p = page) => p.keyboard.press(key),
    sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
    log: (...a) => logs.push(`[test] ${a.join(' ')}`),
  };
  return ctx;
}

export async function getState(page) {
  return page.evaluate(() => {
    const h = window.__bodySurfers;
    if (!h || typeof h.state !== 'function') return null;
    return JSON.parse(JSON.stringify(h.state()));
  });
}

/**
 * Poll state() until pred(state) is truthy. Throws with the last state on timeout.
 * `desc` makes the failure message readable.
 */
export async function waitState(page, pred, { timeout = 10000, interval = 100, desc = 'condition' } = {}) {
  const t0 = Date.now();
  let last = null;
  let lastErr = null;
  while (Date.now() - t0 < timeout) {
    try {
      last = await getState(page);
      if (last && pred(last)) return last;
    } catch (e) {
      lastErr = e;
    }
    await new Promise((r) => setTimeout(r, interval));
  }
  if (last === null) {
    throw new Error(
      `timeout (${timeout} ms) waiting for ${desc}: window.__bodySurfers.state() unavailable` +
        (lastErr ? ` (${lastErr.message})` : ''),
    );
  }
  throw new Error(`timeout (${timeout} ms) waiting for ${desc}; last state: ${JSON.stringify(last)}`);
}

export async function waitFor(fn, { timeout = 10000, interval = 100, desc = 'condition' } = {}) {
  const t0 = Date.now();
  let last;
  while (Date.now() - t0 < timeout) {
    last = await fn();
    if (last) return last;
    await new Promise((r) => setTimeout(r, interval));
  }
  throw new Error(`timeout (${timeout} ms) waiting for ${desc}; last: ${JSON.stringify(last)}`);
}

export function assert(cond, msg) {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
}

export async function readStorage(page, key) {
  return page.evaluate((k) => {
    const raw = localStorage.getItem(k);
    if (raw === null) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return { __corrupt: raw };
    }
  }, key);
}

export async function writeStorage(page, key, value) {
  await page.evaluate(([k, v]) => localStorage.setItem(k, JSON.stringify(v)), [key, value]);
}

/** Visible elements inside #ui-root matching a selector. */
export async function countVisible(page, selector) {
  return page.evaluate((sel) => {
    const root = document.getElementById('ui-root') ?? document.body;
    return [...root.querySelectorAll(sel)].filter((el) => {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && Number(cs.opacity) > 0.05;
    }).length;
  }, selector);
}

export const world = (s, i = 0) => (s && Array.isArray(s.worlds) ? s.worlds[i] : undefined);

/** Navigate to a keyboard autostart run and wait until it is playing. */
export async function startKeyboardRun(ctx, extra = '') {
  await ctx.goto(`/?input=keyboard&autostart=1&mute=1${extra}`);
  return ctx.waitState((s) => s.screen === 'playing', { timeout: 12000, desc: "screen 'playing'" });
}

/** Seed settings so the run is not in tutorial mode (or any other overrides) and reload. */
export async function seedSettings(ctx, patch) {
  await ctx.goto('/?input=keyboard&mute=1');
  await ctx.page.evaluate(
    ([k, p]) => {
      let cur = {};
      try {
        cur = JSON.parse(localStorage.getItem(k) ?? '{}') ?? {};
      } catch {
        cur = {};
      }
      localStorage.setItem(k, JSON.stringify({ ...cur, ...p }));
    },
    [STORAGE_KEYS.settings, patch],
  );
}

export function percentile(sorted, p) {
  if (!sorted.length) return NaN;
  const i = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[i];
}
