// Copies the MediaPipe WASM runtime into public/ so the game runs fully offline
// (no CDN at runtime — venue wifi is unreliable).
import { cpSync, mkdirSync, rmSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = join(root, 'node_modules/@mediapipe/tasks-vision/wasm');
const dest = join(root, 'public/mediapipe/wasm');
mkdirSync(dest, { recursive: true });
// The *_module_internal build is only used by forVisionTasks(path, useModule = true); we don't
// pass that, so skip it (12 MB less to deploy). SIMD + no-SIMD fallback are both needed.
cpSync(src, dest, { recursive: true, filter: (f) => !f.includes('_module_internal') });
for (const f of ['vision_wasm_module_internal.js', 'vision_wasm_module_internal.wasm']) rmSync(join(dest, f), { force: true });
console.log(`copied MediaPipe wasm -> ${dest}`);
