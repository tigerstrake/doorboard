#!/usr/bin/env node
// BODY SURFERS frame-time benchmark.
//
//   node e2e/perf.mjs [--base URL] [--warmup 3000] [--measure 10000] [--grep 2P] [--no-camera] [--json out.json]
//
// Runs ?autopilot=1&autostart=1 in 1P/2P × 1280x720/1920x1080 × quality high/low (keyboard
// input, no vision), plus 1P/2P with Chrome's fake camera (vision running) to sample
// inference ms. After the warm-up it records requestAnimationFrame deltas for --measure ms
// and reports p50/p95/p99, long frames (>33.3 ms and >50 ms) and the run's distance.
// NOTE: headless Chrome uses SwiftShader/ANGLE — absolute numbers are a lower bound of the
// real GPU; compare configurations relative to each other.
import { writeFileSync } from 'node:fs';
import { argHas, argOpt, findFakeVideo, getState, launch, openPage, percentile, resolveBase } from './lib.mjs';

const args = process.argv.slice(2);
const base = await resolveBase(argOpt(args, 'base'));
const WARMUP = Number(argOpt(args, 'warmup', 3000));
const MEASURE = Number(argOpt(args, 'measure', 10000));
const grep = argOpt(args, 'grep');

const configs = [];
for (const players of [1, 2])
  for (const [w, h] of [[1280, 720], [1920, 1080]])
    for (const quality of ['high', 'low']) configs.push({ players, w, h, quality, camera: false });
if (!argHas(args, 'no-camera'))
  for (const players of [1, 2]) configs.push({ players, w: 1280, h: 720, quality: 'high', camera: true });
const label = (c) => `${c.players}P ${c.w}x${c.h} ${c.quality}${c.camera ? ' +camera' : ''}`;
const selected = configs.filter((c) => !grep || new RegExp(grep, 'i').test(label(c)));

console.log(`perf against ${base}: warm-up ${WARMUP} ms, measure ${MEASURE} ms\n`);
const browsers = {};
const rows = [];
for (const c of selected) {
  const key = c.camera ? 'cam' : 'plain';
  browsers[key] ??= await launch({ camera: c.camera, fakeVideo: c.camera ? findFakeVideo() : null });
  const ctx = await openPage(browsers[key], base, { camera: c.camera, width: c.w, height: c.h });
  const row = { config: label(c) };
  const hardTimeout = setTimeout(() => {
    console.log(`${row.config}: hard timeout, closing context`);
    ctx.context.close().catch(() => {});
  }, (c.camera ? 45000 : 20000) + WARMUP + MEASURE + 15000);
  try {
    const q = `/?autostart=1&autopilot=1&mute=1&debug=1&seed=42&players=${c.players}&quality=${c.quality}${c.camera ? '' : '&input=keyboard'}`;
    await ctx.goto(q);
    await ctx.waitState((s) => s.screen === 'playing' || s.screen === 'paused', { timeout: c.camera ? 40000 : 15000, desc: 'run started' });
    await ctx.sleep(WARMUP);
    const s0 = await getState(ctx.page);
    // sample inference ms while measuring (camera configs)
    const infer = [];
    const sampler = c.camera
      ? setInterval(async () => {
          try {
            const v = await ctx.page.evaluate(() => {
              const a = window.__bodySurfers?.app;
              const st = window.__bodySurfers?.state?.();
              return st?.inferenceMs ?? st?.vision?.inferenceMs ?? a?.frame?.inferenceMs ?? a?.debug?.inferenceMs ?? null;
            });
            if (typeof v === 'number' && v > 0) infer.push(v);
          } catch {
            /* ignore */
          }
        }, 250)
      : null;
    const deltas = await ctx.page.evaluate(
      (ms) =>
        new Promise((resolve) => {
          const out = [];
          let last = performance.now();
          const end = last + ms;
          // safety net: if rAF stalls (hidden page / hung GPU) resolve with what we have
          setTimeout(() => resolve(out.length ? out : [ms]), ms + 8000);
          const tick = (t) => {
            out.push(t - last);
            last = t;
            if (t < end) requestAnimationFrame(tick);
            else resolve(out);
          };
          requestAnimationFrame((t) => {
            last = t;
            requestAnimationFrame(tick);
          });
        }),
      MEASURE,
    );
    if (sampler) clearInterval(sampler);
    const s1 = await getState(ctx.page);
    const sorted = [...deltas].sort((a, b) => a - b);
    const mean = deltas.reduce((a, b) => a + b, 0) / deltas.length;
    Object.assign(row, {
      frames: deltas.length,
      fps: +(1000 / mean).toFixed(1),
      p50: +percentile(sorted, 50).toFixed(1),
      p95: +percentile(sorted, 95).toFixed(1),
      p99: +percentile(sorted, 99).toFixed(1),
      max: +sorted[sorted.length - 1].toFixed(1),
      'long>33': deltas.filter((d) => d > 33.4).length,
      'long>50': deltas.filter((d) => d > 50).length,
      inferMs: infer.length ? +(infer.reduce((a, b) => a + b, 0) / infer.length).toFixed(1) : c.camera ? 'n/a' : '-',
      screen: s1?.screen,
      dDist: s0 && s1 ? +(Math.max(...s1.worlds.map((w) => w.distance)) - Math.max(...s0.worlds.map((w) => w.distance))).toFixed(0) : 'n/a',
      errors: ctx.errors.length,
    });
  } catch (e) {
    row.error = e.message.split('\n')[0].slice(0, 160);
    row.errors = ctx.errors.length;
    if (ctx.errors.length) row.firstError = ctx.errors[0].slice(0, 200);
  }
  clearTimeout(hardTimeout);
  await ctx.context.close().catch(() => {});
  rows.push(row);
  console.log(row.error ? `${row.config.padEnd(26)} ERROR ${row.error}${row.firstError ? `\n${"".padEnd(27)}${row.firstError}` : ""}` : `${row.config.padEnd(26)} p50 ${row.p50}  p95 ${row.p95}  p99 ${row.p99}  fps ${row.fps}  long>50 ${row['long>50']}  infer ${row.inferMs}`);
}
for (const b of Object.values(browsers)) await b.close().catch(() => {});
console.log('');
console.table(rows);
const out = argOpt(args, 'json');
if (out) writeFileSync(out, JSON.stringify(rows, null, 2));
process.exit(rows.some((r) => r.error) ? 1 : 0);
