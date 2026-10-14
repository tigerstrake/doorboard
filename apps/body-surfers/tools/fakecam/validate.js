// Headless validation: render the routine frame-by-frame (deterministic sim time) and run
// MediaPipe PoseLandmarker (VIDEO mode, CPU) on every frame. Results → window.__result.
import { FilesetResolver, PoseLandmarker } from '@mediapipe/tasks-vision';
import { createWorld } from './scene.js';

const q = new URLSearchParams(location.search);
const routine = q.get('routine') ?? 'solo';
const fps = Number(q.get('fps') ?? 15);
const model = q.get('model') ?? 'lite';
const world = createWorld({ routine, canvas: document.getElementById('c'), width: 1280, height: 720 });
window.__world = world;

const fileset = await FilesetResolver.forVisionTasks('/mediapipe/wasm');
const lm = await PoseLandmarker.createFromOptions(fileset, {
  baseOptions: { modelAssetPath: `/models/pose_landmarker_${model}.task`, delegate: 'CPU' },
  runningMode: 'VIDEO', numPoses: 3,
  minPoseDetectionConfidence: 0.5, minPosePresenceConfidence: 0.5, minTrackingConfidence: 0.5,
});

const dur = Number(q.get('seconds') ?? world.duration);
const frames = [];
for (let i = 0; i * (1 / fps) <= dur; i++) {
  const t = i / fps;
  const info = world.renderAt(t);
  const res = lm.detectForVideo(world.canvas, Math.round(t * 1000) + 1);
  const poses = res.landmarks.map((L) => ({
    nose: [L[0].x, L[0].y], vis: L[0].visibility ?? 0,
    sh: [(L[11].x + L[12].x) / 2, (L[11].y + L[12].y) / 2],
    hip: [(L[23].x + L[24].x) / 2, (L[23].y + L[24].y) / 2],
    wristY: Math.min(L[15].y, L[16].y),
    shW: Math.hypot(L[11].x - L[12].x, L[11].y - L[12].y),
  }));
  // match each synthetic actor to the nearest detected nose
  const actors = info.actors.map((a) => {
    let best = null, bd = 1e9;
    for (const p of poses) { const d = Math.hypot(p.nose[0] - a.head[0], p.nose[1] - a.head[1]); if (d < bd) { bd = d; best = p; } }
    return { phase: a.phase, spectator: a.spectator, hit: !!best && bd < 0.08, pose: bd < 0.08 ? best : null };
  });
  frames.push({ t, n: poses.length, actors });
  if (i % 30 === 0) await new Promise((r) => setTimeout(r, 0));
}
lm.close();
window.__result = { routine, fps, model, frames };
