#!/usr/bin/env node
// BODY SURFERS e2e scenario runner.
//
//   node e2e/run.mjs                       # all scenarios
//   node e2e/run.mjs --grep pause          # scenarios whose name matches /pause/i
//   node e2e/run.mjs --base http://localhost:5187
//   node e2e/run.mjs --list
//
// Each scenario lives in e2e/scenarios/*.mjs and exports
//   default { name, camera?: boolean, timeout?: ms, run(ctx) }
// A scenario fails if run() throws OR the page logged any page error / console.error.
// On failure a screenshot + full console dump go to shots/e2e/<name>.{png,log}.
import { mkdirSync, readdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ROOT, SHOTS_DIR, argHas, argOpt, findFakeVideo, launch, openPage, resolveBase } from './lib.mjs';

const args = process.argv.slice(2);
const grep = argOpt(args, 'grep');
const dir = join(ROOT, 'e2e', 'scenarios');
const files = readdirSync(dir).filter((f) => f.endsWith('.mjs')).sort();
const scenarios = [];
for (const f of files) {
  const mod = await import(pathToFileURL(join(dir, f)).href);
  const s = mod.default;
  if (!s || typeof s.run !== 'function') continue;
  s.file = f;
  if (grep && !new RegExp(grep, 'i').test(s.name) && !new RegExp(grep, 'i').test(f)) continue;
  scenarios.push(s);
}
if (argHas(args, 'list')) {
  for (const s of scenarios) console.log(`${s.name}  (${s.file})`);
  process.exit(0);
}
if (!scenarios.length) {
  console.error(`no scenarios match ${grep}`);
  process.exit(2);
}

const base = await resolveBase(argOpt(args, 'base'));
const fakeVideo = findFakeVideo();
console.log(`e2e against ${base}  (${scenarios.length} scenario${scenarios.length > 1 ? 's' : ''})${fakeVideo ? `  fake video: ${fakeVideo}` : ''}\n`);
mkdirSync(SHOTS_DIR, { recursive: true });

const browsers = {};
const getBrowser = async (camera) => {
  const k = camera ? 'camera' : 'plain';
  if (!browsers[k]) browsers[k] = await launch({ camera, fakeVideo: camera && process.env.E2E_FAKE_VIDEO ? fakeVideo : null });
  return browsers[k];
};

const results = [];
for (const s of scenarios) {
  const t0 = Date.now();
  let ctx = null;
  let err = null;
  try {
    const browser = await getBrowser(!!s.camera);
    ctx = await openPage(browser, base, { camera: !!s.camera });
    const timeout = s.timeout ?? 60000;
    let timer;
    await Promise.race([
      s.run(ctx),
      new Promise((_, rej) => (timer = setTimeout(() => rej(new Error(`scenario timeout ${timeout} ms`)), timeout))),
    ]).finally(() => clearTimeout(timer));
    await new Promise((r) => setTimeout(r, 200)); // let late errors land
    if (ctx.errors.length) {
      err = new Error(`${ctx.errors.length} page error(s):\n    ${ctx.errors.slice(0, 8).join('\n    ')}`);
    }
  } catch (e) {
    err = e;
    if (ctx?.errors.length) err.message += `\n  page errors:\n    ${ctx.errors.slice(0, 8).join('\n    ')}`;
  }
  if (err && ctx?.unexpectedReloads > 0) err.message += `\n  NOTE: page reloaded ${ctx.unexpectedReloads}x mid-scenario (Vite HMR full reload?) — may be flaky, re-run`;
  const ms = Date.now() - t0;
  const slug = s.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase();
  if (err && ctx) {
    for (let i = 0; i < ctx.pages.length; i++) {
      try {
        await ctx.pages[i].screenshot({ path: join(SHOTS_DIR, `${slug}${i ? `-${i}` : ''}.png`), timeout: 5000 });
      } catch {
        /* page may be gone */
      }
    }
    writeFileSync(join(SHOTS_DIR, `${slug}.log`), `${err.stack ?? err.message}\n\n${ctx.logs.join('\n')}\n`);
  }
  await ctx?.context.close().catch(() => {});
  results.push({ name: s.name, ok: !err, ms, err });
  const line = `${err ? 'FAIL' : 'PASS'}  ${s.name.padEnd(34)} ${(ms / 1000).toFixed(1).padStart(5)} s`;
  console.log(line);
  if (err) console.log(`      ${String(err.message).split('\n').join('\n      ')}\n      -> shots/e2e/${slug}.png / .log`);
}

for (const b of Object.values(browsers)) await b.close().catch(() => {});
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed} passed, ${failed} failed  (${(results.reduce((a, r) => a + r.ms, 0) / 1000).toFixed(1)} s)`);
process.exit(failed ? 1 : 0);
