#!/usr/bin/env node
// Headless browser check: loads a page in system Chrome (via playwright-core),
// optionally presses keys on a schedule, takes screenshots, and reports console
// errors / page errors. Exit code 1 if the page threw uncaught errors.
//
// Usage:
//   node scripts/shot.mjs --url http://localhost:5173/sandbox/render.html --out shots/render.png
//   node scripts/shot.mjs --url "http://localhost:5173/?input=keyboard&autostart=1" \
//        --shots 1500,4000,8000 --keys "ArrowLeft@2000,Space@3000" --out shots/game.png
//
// Options:
//   --url <url>            page to load (required)
//   --out <file.png>       screenshot path (default shots/shot.png). With --shots, files get -<ms> suffixes.
//   --wait <ms>            single screenshot after this delay (default 2500)
//   --shots <ms,ms,...>    take several screenshots at these times (ms after load)
//   --keys <K@ms,...>      press keys at times, e.g. "ArrowLeft@1000,Space@1500,KeyW@2000"
//   --hold <K@ms:dur,...>  hold keys down for dur ms
//   --width/--height       viewport (default 1280x720)
//   --fake-camera          grant camera permission + use Chrome's synthetic camera feed
//   --eval <js>            evaluate an expression at the end and print the JSON result
//   --logs                 also print console.log/info output (errors & warnings always print)
import { chromium } from 'playwright-core';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

const args = process.argv.slice(2);
const opt = (name, def) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && i + 1 < args.length && !args[i + 1].startsWith('--') ? args[i + 1] : def;
};
const has = (name) => args.includes(`--${name}`);

const url = opt('url');
if (!url) {
  console.error('missing --url');
  process.exit(2);
}
const out = opt('out', 'shots/shot.png');
const width = Number(opt('width', 1280));
const height = Number(opt('height', 720));
const shotTimes = opt('shots') ? opt('shots').split(',').map(Number) : [Number(opt('wait', 2500))];
const parseSchedule = (s) =>
  (s ? s.split(',') : []).filter(Boolean).map((tok) => {
    const [key, rest] = tok.split('@');
    const [at, dur] = rest.split(':');
    return { key, at: Number(at), dur: dur ? Number(dur) : 0 };
  });
const keyEvents = [...parseSchedule(opt('keys')), ...parseSchedule(opt('hold'))];

const chromeArgs = ['--enable-webgl', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader', '--autoplay-policy=no-user-gesture-required'];
if (has('fake-camera')) chromeArgs.push('--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream');

const browser = await chromium.launch({ channel: 'chrome', headless: true, args: chromeArgs });
const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1 });
if (has('fake-camera')) await context.grantPermissions(['camera'], { origin: new URL(url).origin });
const page = await context.newPage();

let pageErrors = 0;
const printLogs = has('logs');
page.on('console', (msg) => {
  const type = msg.type();
  if (type === 'error' || type === 'warning' || printLogs) console.log(`[console.${type}] ${msg.text()}`);
});
page.on('pageerror', (err) => {
  pageErrors++;
  console.log(`[pageerror] ${err.message}\n${err.stack ?? ''}`);
});
page.on('requestfailed', (req) => console.log(`[requestfailed] ${req.url()} ${req.failure()?.errorText ?? ''}`));

const t0 = Date.now();
await page.goto(url, { waitUntil: 'load', timeout: 30000 });

const timeline = [
  ...keyEvents.map((k) => ({ at: k.at, run: async () => {
    if (k.dur > 0) {
      await page.keyboard.down(k.key);
      setTimeout(() => page.keyboard.up(k.key).catch(() => {}), k.dur);
    } else {
      await page.keyboard.press(k.key);
    }
  } })),
  ...shotTimes.map((ms, i) => ({ at: ms, run: async () => {
    const file = shotTimes.length > 1 ? out.replace(/\.png$/, `-${ms}.png`) : out;
    mkdirSync(dirname(file), { recursive: true });
    await page.screenshot({ path: file });
    console.log(`[shot] ${file}`);
  } })),
].sort((a, b) => a.at - b.at);

for (const ev of timeline) {
  const wait = ev.at - (Date.now() - t0);
  if (wait > 0) await page.waitForTimeout(wait);
  await ev.run();
}

const expr = opt('eval');
if (expr) {
  try {
    const result = await page.evaluate((e) => {
      // eslint-disable-next-line no-eval
      const v = (0, eval)(e);
      return JSON.stringify(v, null, 2);
    }, expr);
    console.log(`[eval] ${result}`);
  } catch (e) {
    console.log(`[eval-error] ${e.message}`);
  }
}

await browser.close();
console.log(`[done] pageErrors=${pageErrors}`);
process.exit(pageErrors > 0 ? 1 : 0);
