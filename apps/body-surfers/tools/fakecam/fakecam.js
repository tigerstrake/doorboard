// Live fake camera: renders the scripted synthetic person in real time onto a canvas and
// returns canvas.captureStream(). Loaded on demand by the init script that run.mjs injects
// (getUserMedia override). State is mirrored to window.__fakecam for the runner to poll.
import { createWorld } from './scene.js';

let world = null;
let t0 = 0;

export async function startFakeCam(cfg = {}, constraints = {}) {
  if (!world) {
    const v = typeof constraints.video === 'object' ? constraints.video : {};
    const num = (c, d) => (typeof c === 'number' ? c : c?.ideal ?? c?.exact ?? c?.max ?? d);
    const width = cfg.width ?? num(v.width, 1280), height = cfg.height ?? num(v.height, 720);
    world = createWorld({ routine: cfg.routine ?? 'solo', width, height });
    const state = (window.__fakecam = { routine: world.routine, duration: world.duration, t: 0, phase: 'start', phases: [], frames: 0, fps: 0, width, height });
    t0 = performance.now() + (cfg.startDelayMs ?? 0);
    let fpsT = performance.now(), fpsN = 0;
    const tick = () => {
      let t = Math.max(0, (performance.now() - t0) / 1000);
      if (cfg.loop) t %= world.duration;
      const info = world.renderAt(t);
      state.t = t; state.frames++; fpsN++;
      const label = info.actors.map((a) => a.phase).join('|');
      if (label !== state.phase) { state.phase = label; state.phases.push({ t: +t.toFixed(2), phase: label }); }
      const now = performance.now();
      if (now - fpsT > 1000) { state.fps = +((fpsN * 1000) / (now - fpsT)).toFixed(1); fpsT = now; fpsN = 0; }
    };
    tick();
    // rAF when visible; setTimeout keeps rendering if the tab is throttled/hidden
    const loop = () => { tick(); if (document.visibilityState === 'hidden') setTimeout(loop, 1000 / (cfg.fps ?? 30)); else requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  }
  const stream = world.canvas.captureStream(cfg.fps ?? 30);
  const track = stream.getVideoTracks()[0];
  const settings = track.getSettings.bind(track);
  track.getSettings = () => ({ ...settings(), deviceId: 'fakecam', groupId: 'fakecam', width: world.canvas.width, height: world.canvas.height, frameRate: cfg.fps ?? 30, facingMode: 'user' });
  Object.defineProperty(track, 'label', { value: `FakeCam (${world.routine})` });
  return stream;
}
