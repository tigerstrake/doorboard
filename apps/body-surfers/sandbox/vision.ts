// Vision sandbox: live debug page for the VisionSystem.
// ?autostart=1 starts model + camera and calibrates the first person automatically.
import { VisionSystem } from '../src/vision';
import type { PlayerId, PoseModel, VisionFrame, VisionTuning } from '../src/shared/types';
import { PLAYER_COLORS } from '../src/shared/constants';
import { LM, SKELETON_CONNECTIONS } from '../src/shared/pose';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const vision = new VisionSystem();
(window as unknown as { __vision: VisionSystem }).__vision = vision;

const canvas = $<HTMLCanvasElement>('view');
const ctx = canvas.getContext('2d') as CanvasRenderingContext2D;
const statusEl = $('status');
const slotsEl = $('slots');
const logEl = $('log');
let model: PoseModel = 'lite';
let players: 1 | 2 = 1;
let locked = false;
const params = new URLSearchParams(location.search);
const autostart = params.get('autostart') === '1';

// ---- event log ----
const logLines: string[] = [];
function log(s: string): void {
  logLines.push(`${(performance.now() / 1000).toFixed(2).padStart(7)}  ${s}`);
  if (logLines.length > 200) logLines.shift();
  logEl.textContent = logLines.join('\n');
  logEl.scrollTop = logEl.scrollHeight;
}
// Machine-readable log for headless runs (node tools/fakecam/run.mjs --eval "__visionLog").
interface LogEntry { t: number; ft: number | null; slot: number; ev: string }
const fakeT = (): number | null => {
  const fc = (window as unknown as { __fakecam?: { t: number } }).__fakecam;
  return fc ? Math.round(fc.t * 100) / 100 : null;
};
const visionLog: LogEntry[] = [];
const visionStats = { maxHandsUp: [0, 0], calibratedAt: [null, null] as (number | null)[], msSamples: [] as number[], people: [0, 0, 0, 0], xRange: [[1, 0], [1, 0]] as number[][] };
Object.assign(window, { __visionLog: visionLog, __visionStats: visionStats });
vision.onAction((e) => {
  const a = e.action;
  visionLog.push({ t: Math.round(e.t), ft: fakeT(), slot: e.playerId, ev: a.type === 'lane' ? `lane ${a.lane}` : a.type });
  log(`P${e.playerId + 1} ${a.type}${a.type === 'lane' ? ' ' + a.lane : ''}`);
});

// ---- tuning sliders ----
const sliders: Array<[keyof VisionTuning, number, number, number]> = [
  ['laneThreshold', 0.3, 2, 0.05],
  ['laneHysteresis', 0, 0.8, 0.05],
  ['jumpThreshold', 0.1, 1, 0.02],
  ['duckThreshold', 0.1, 1.2, 0.02],
  ['smoothing', 0, 1, 0.05],
];
const tuningEl = $('tuning');
for (const [key, min, max, step] of sliders) {
  const row = document.createElement('div');
  row.className = 'row';
  const val = vision.getTuning()[key] as number;
  row.innerHTML = `<label>${key}</label><input type="range" min="${min}" max="${max}" step="${step}" value="${val}"><output>${val}</output>`;
  const input = row.querySelector('input') as HTMLInputElement;
  const out = row.querySelector('output') as HTMLOutputElement;
  input.addEventListener('input', () => {
    vision.setTuning({ [key]: Number(input.value) });
    out.textContent = input.value;
  });
  tuningEl.appendChild(row);
}
{
  const row = document.createElement('div');
  row.className = 'row';
  row.innerHTML = `<label>mirror</label><input type="checkbox" checked>`;
  const cb = row.querySelector('input') as HTMLInputElement;
  cb.addEventListener('change', () => vision.setTuning({ mirror: cb.checked }));
  tuningEl.appendChild(row);
}

// ---- controls ----
async function start(): Promise<void> {
  try {
    const cams = $<HTMLSelectElement>('cams');
    const deviceId = cams.value || null;
    // Model first: GPU shader compilation blocks the main thread for seconds.
    await vision.loadModel(model);
    await vision.startCamera(deviceId);
    log(`started (${vision.delegate ?? '?'} delegate)`);
    const list = await vision.listCameras();
    const cur = cams.value;
    cams.replaceChildren(
      ...list.map((d, i) => {
        const opt = document.createElement('option');
        opt.value = d.deviceId;
        opt.textContent = d.label || `camera ${i + 1}`;
        return opt;
      }),
    );
    if (cur) cams.value = cur;
  } catch (e) {
    log(`ERROR ${e instanceof Error ? e.message : String(e)}`);
  }
}
$('start').addEventListener('click', () => void start());
$<HTMLSelectElement>('cams').addEventListener('change', () => void vision.startCamera($<HTMLSelectElement>('cams').value || null).catch((e) => log(`ERROR ${String(e)}`)));
const setPlayers = (n: 1 | 2) => {
  players = n;
  vision.setPlayerCount(n);
  $('p1').classList.toggle('on', n === 1);
  $('p2').classList.toggle('on', n === 2);
};
$('p1').addEventListener('click', () => setPlayers(1));
$('p2').addEventListener('click', () => setPlayers(2));
const setLocked = (l: boolean) => {
  locked = l;
  vision.lockSlots(l);
  $('lock').classList.toggle('on', l);
};
$('lock').addEventListener('click', () => setLocked(!locked));
function calibrate(): void {
  setLocked(true);
  for (let s = 0; s < players; s++) vision.startCalibration(s as PlayerId);
  log('calibrating');
}
$('calib').addEventListener('click', calibrate);
$('reset').addEventListener('click', () => vision.resetCalibration());
$('selfieBtn').addEventListener('click', () => {
  const url = vision.captureSelfie(0);
  const img = $<HTMLImageElement>('selfie');
  if (url) {
    img.src = url;
    $('selfieInfo').textContent = `${Math.round((url.length * 3) / 4 / 1024)} KB`;
  } else $('selfieInfo').textContent = 'no person in slot 0';
});
$('model').addEventListener('click', () => {
  model = model === 'lite' ? 'full' : 'lite';
  $('model').textContent = `Model: ${model}`;
  vision.loadModel(model).then(() => log(`model ${model} (${vision.delegate})`), (e) => log(`ERROR ${String(e)}`));
});

// ---- drawing ----
function draw(frame: VisionFrame | null): void {
  const video = vision.getVideo();
  const W = canvas.width;
  const H = canvas.height;
  const mirror = vision.getTuning().mirror;
  if (video && video.videoWidth && (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight)) {
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
  }
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, W, H);
  if (video && video.readyState >= 2) {
    ctx.save();
    if (mirror) {
      ctx.translate(W, 0);
      ctx.scale(-1, 1);
    }
    ctx.drawImage(video, 0, 0, W, H);
    ctx.restore();
  }
  if (!frame) return;
  for (const g of frame.gestures) {
    if (!g) continue;
    const col = PLAYER_COLORS[g.slot];
    ctx.globalAlpha = 0.18;
    ctx.fillStyle = col;
    const hw = g.laneHalfWidth * W;
    const cx = g.centerX * W;
    ctx.fillRect(cx - hw, 0, 2 * hw, H);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = col;
    ctx.lineWidth = 2;
    ctx.setLineDash([8, 6]);
    for (const x of [cx - hw, cx + hw]) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, H);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    if (g.phase !== 'uncalibrated' && g.phase !== 'calibrating') {
      const lines: Array<[number, string]> = [[g.jumpLineY, '#5f5'], [g.baselineY, '#fff'], [g.duckLineY, '#f55']];
      for (const [y, c] of lines) {
        ctx.strokeStyle = c;
        ctx.beginPath();
        ctx.moveTo(cx - 3 * hw, y * H);
        ctx.lineTo(cx + 3 * hw, y * H);
        ctx.stroke();
      }
    }
  }
  for (const p of frame.people) {
    const col = PLAYER_COLORS[p.slot];
    ctx.strokeStyle = col;
    ctx.lineWidth = 4;
    for (const [a, b] of SKELETON_CONNECTIONS) {
      const la = p.landmarks[a];
      const lb = p.landmarks[b];
      if (la.visibility < 0.3 || lb.visibility < 0.3) continue;
      ctx.beginPath();
      ctx.moveTo(la.x * W, la.y * H);
      ctx.lineTo(lb.x * W, lb.y * H);
      ctx.stroke();
    }
    const nose = p.landmarks[LM.NOSE];
    ctx.beginPath();
    ctx.arc(nose.x * W, nose.y * H, p.scale * W * 0.35, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = '#ff0';
    ctx.beginPath();
    ctx.arc(p.center.x * W, p.center.y * H, 7, 0, Math.PI * 2);
    ctx.fill();
    if (p.handsUpProgress > 0) {
      ctx.strokeStyle = '#ff0';
      ctx.lineWidth = 6;
      ctx.beginPath();
      ctx.arc(nose.x * W, nose.y * H - p.scale * W, 22, -Math.PI / 2, -Math.PI / 2 + p.handsUpProgress * Math.PI * 2);
      ctx.stroke();
    }
  }
}

function renderPanel(frame: VisionFrame | null): void {
  const st = vision.status;
  statusEl.className = st === 'error' ? 'error' : '';
  const fps = frame ? frame.fps.toFixed(1) : '0';
  const ms = frame ? frame.inferenceMs.toFixed(1) : '-';
  statusEl.textContent =
    `status: ${st}${vision.error ? '\n' + vision.error : ''}\n` +
    `inference fps: ${fps}  ms: ${ms}  camera fps: ${vision.cameraFps.toFixed(0)}\n` +
    `delegate: ${vision.delegate ?? '-'}  model: ${model}  players: ${players}  locked: ${locked}\n` +
    `people: ${frame?.people.length ?? 0}  spectators: ${frame?.extraPeople ?? 0}`;
  statusEl.dataset.status = st;
  statusEl.dataset.fps = fps;
  let html = '';
  for (let s = 0; s < players; s++) {
    const g = frame?.gestures[s] ?? null;
    const p = frame?.people.find((q) => q.slot === s);
    html +=
      `<div class="slot" style="border-left:4px solid ${PLAYER_COLORS[s]}">P${s + 1}: ` +
      (g
        ? `${g.phase} ${g.phase === 'calibrating' ? Math.round(g.calibrationProgress * 100) + '%' : ''} lane ${g.lane}\n`
        : 'no detector state\n') +
      (p
        ? `scale ${p.scale.toFixed(3)} conf ${p.confidence.toFixed(2)} hands ${Math.round(p.handsUpProgress * 100)}%\nhints: ${p.hints.join(', ') || '-'}`
        : 'not in frame') +
      `</div>`;
  }
  if (slotsEl.innerHTML !== html) slotsEl.innerHTML = html;
}

let autoCalibrated = false;
let lastStatsFrame: VisionFrame | null = null;
function loop(now: number): void {
  const frame = vision.update(now);
  if (frame) {
    for (const p of frame.people) {
      visionStats.maxHandsUp[p.slot] = Math.max(visionStats.maxHandsUp[p.slot], p.handsUpProgress);
      const r = visionStats.xRange[p.slot];
      r[0] = Math.min(r[0], p.center.x);
      r[1] = Math.max(r[1], p.center.x);
    }
    for (const g of frame.gestures) if (g && g.phase === 'ready' && visionStats.calibratedAt[g.slot] === null) visionStats.calibratedAt[g.slot] = fakeT();
    if (frame !== lastStatsFrame) {
      lastStatsFrame = frame;
      visionStats.msSamples.push(Math.round(frame.inferenceMs * 10) / 10);
      visionStats.people[Math.min(3, frame.people.length + frame.extraPeople)]++;
    }
  }
  draw(frame);
  renderPanel(frame);
  if (autostart && !autoCalibrated && frame && frame.people.length >= players) {
    autoCalibrated = true;
    calibrate();
  }
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);

if (autostart) {
  if (params.get('players') === '2') setPlayers(2);
  void start().then(() => {
    // Debug: ?numposes=N overrides the landmarker's numPoses (cost measurements).
    const np = Number(params.get('numposes'));
    if (np >= 1 && np <= 3) {
      const lm = (vision as unknown as { landmarker: { setOptions(o: { numPoses: number }): Promise<void> } | null }).landmarker;
      void lm?.setOptions({ numPoses: np }).then(() => log(`numPoses=${np}`));
    }
    // Calibrate now; detectors wait for the person (locked slots acquire the largest body).
    if (!autoCalibrated) {
      autoCalibrated = true;
      calibrate();
    }
  });
}
