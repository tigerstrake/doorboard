// Webcam overlay: (mirrored) live video, per-person skeletons in player colors, calibrated
// lane zones (dashed boundaries + active tint), jump/duck lines, gesture flashes, tracking
// dot, hands-up ring, spectator count, and a friendly placeholder while there is no video.
//
// The element is re-parented by the UIManager into a "camslot" of the active screen (lobby,
// countdown, paused) or docked in a corner while playing. Drawing happens every frame while
// visible; the DOM bits around it (status label, spectators) are cached bindings.
import type { GestureState, Lane, TrackedPerson, UIModel } from '../../shared/types';
import { PLAYER_COLORS, PLAYER_LABELS } from '../../shared/constants';
import { LM, SKELETON_CONNECTIONS } from '../../shared/pose';
import { Attr, h, icon, Show, Text } from '../dom';
import { ICONS } from '../icons';
import { CAMERA_STATUS_COPY } from '../copy';
import { CAM_MAX_BACKING_W, CAM_MAX_DPR, CAM_MIN_VISIBILITY, type CamMode, GESTURE_FLASH_MS } from '../config';

const NAVY = '#1b1b2f';
const GREEN = '#2ed573';
const ORANGE = '#ff9f1a';
const DEFAULT_ASPECT = 4 / 3;

interface FlashState {
  t: number;
  text: string;
  prevLane: Lane;
}

export class WebcamOverlay {
  readonly root: HTMLElement;
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D | null;
  private readonly modeAttr: Attr;
  private readonly placeholder: Show;
  private readonly placeholderText: Text;
  private readonly spectators: Show;
  private readonly spectatorsText: Text;
  private readonly live: Show;
  private mode: CamMode = 'hidden';
  private aspect = DEFAULT_ASPECT;
  private cssW = 0;
  private cssH = 0;
  private dirtySize = true;
  private readonly flashes: [FlashState, FlashState] = [
    { t: -1, text: '', prevLane: 0 },
    { t: -1, text: '', prevLane: 0 },
  ];
  private readonly ro: ResizeObserver | null = null;

  constructor() {
    this.canvas = h('canvas', 'bs-cam__canvas');
    this.ctx = this.canvas.getContext('2d');
    const phText = h('span', 'bs-cam__phtext');
    const ph = h('div', 'bs-cam__placeholder', icon(ICONS.cameraOff, 'bs-cam__phicon'), phText);
    const specText = h('span', null);
    const spec = h('div', 'bs-cam__spec', icon(ICONS.eye, 'bs-cam__specicon'), specText);
    const live = h('div', 'bs-cam__live', h('i', null), 'LIVE');
    this.root = h('div', 'bs-cam', this.canvas, ph, spec, live);
    this.root.style.setProperty('--cam-aspect', String(DEFAULT_ASPECT));
    this.modeAttr = new Attr(this.root, 'data-mode');
    this.modeAttr.set('hidden');
    this.placeholder = new Show(ph);
    this.placeholderText = new Text(phText);
    this.spectators = new Show(spec);
    this.spectatorsText = new Text(specText);
    this.live = new Show(live);
    if (typeof ResizeObserver !== 'undefined') {
      this.ro = new ResizeObserver(() => {
        this.dirtySize = true;
      });
      this.ro.observe(this.root);
    }
  }

  setMode(mode: CamMode): void {
    if (mode === this.mode) return;
    this.mode = mode;
    this.modeAttr.set(mode);
    this.dirtySize = true;
  }

  private ensureSize(): void {
    if (!this.dirtySize) return;
    this.dirtySize = false;
    const r = this.root.getBoundingClientRect();
    this.cssW = r.width;
    this.cssH = r.height;
    const dpr = Math.min(CAM_MAX_DPR, window.devicePixelRatio || 1);
    const w = Math.max(2, Math.min(CAM_MAX_BACKING_W, Math.round(r.width * dpr)));
    const hh = Math.max(2, Math.round(w / this.aspect));
    if (this.canvas.width !== w) this.canvas.width = w;
    if (this.canvas.height !== hh) this.canvas.height = hh;
  }

  update(m: UIModel, now: number): void {
    if (this.mode === 'hidden') return;
    const video = m.vision.video;
    const hasVideo = video !== null && video.readyState >= 2 && video.videoWidth > 0;
    if (hasVideo && video) {
      const a = video.videoWidth / video.videoHeight;
      if (Math.abs(a - this.aspect) > 0.01) {
        this.aspect = a;
        this.root.style.setProperty('--cam-aspect', a.toFixed(4));
        this.dirtySize = true;
      }
    }
    this.ensureSize();
    const frame = m.vision.frame;
    this.placeholder.set(!hasVideo);
    if (!hasVideo) {
      this.placeholderText.set(m.inputMode === 'keyboard' ? 'KEYBOARD MODE' : CAMERA_STATUS_COPY[m.vision.status]);
    }
    this.live.set(hasVideo && m.vision.status === 'running');
    const spec = frame ? frame.extraPeople : 0;
    this.spectators.set(spec > 0);
    if (spec > 0) this.spectatorsText.set(String(spec));

    const ctx = this.ctx;
    if (!ctx || this.cssW < 4 || this.cssH < 4) return;
    const W = this.canvas.width;
    const H = this.canvas.height;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    if (hasVideo && video) {
      if (m.settings.vision.mirror) {
        ctx.setTransform(-1, 0, 0, 1, W, 0);
        ctx.drawImage(video, 0, 0, W, H);
        ctx.setTransform(1, 0, 0, 1, 0, 0);
      } else {
        ctx.drawImage(video, 0, 0, W, H);
      }
      ctx.fillStyle = 'rgba(16, 22, 48, 0.18)';
      ctx.fillRect(0, 0, W, H);
    } else {
      const g = ctx.createLinearGradient(0, 0, 0, H);
      g.addColorStop(0, '#2a3a7a');
      g.addColorStop(1, '#151c3d');
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, W, H);
    }
    if (!frame) return;

    const k = W / Math.max(1, this.cssW); // backing px per CSS px
    const big = this.mode === 'lobby' || this.mode === 'countdown' || this.mode === 'paused';
    const lw = Math.max(2, (big ? 5 : 3.5) * k);

    // Zones + lines first (under the skeletons).
    for (let s = 0; s < 2; s++) {
      const g = frame.gestures[s];
      if (!g || g.phase === 'uncalibrated' || s >= m.playerCount) continue;
      this.drawZones(ctx, g, W, H, lw, s);
    }
    for (let i = 0; i < frame.people.length; i++) {
      const p = frame.people[i];
      this.drawPerson(ctx, p, W, H, lw, m.screen === 'lobby' || m.screen === 'gameover');
    }
    for (let s = 0; s < 2; s++) {
      const g = frame.gestures[s];
      if (!g) continue;
      this.drawFlash(ctx, g, frame.people, W, H, now, big, k);
    }
  }

  private drawZones(ctx: CanvasRenderingContext2D, g: GestureState, W: number, H: number, lw: number, slot: number): void {
    const color = PLAYER_COLORS[slot];
    const cx = g.centerX * W;
    const hw = g.laneHalfWidth * W;
    const left = cx - hw;
    const right = cx + hw;
    // Active zone tint.
    ctx.globalAlpha = 0.2;
    ctx.fillStyle = color;
    if (g.lane === -1) ctx.fillRect(Math.max(0, left - hw * 2), 0, Math.min(left, hw * 2), H);
    else if (g.lane === 1) ctx.fillRect(right, 0, Math.min(W - right, hw * 2), H);
    else ctx.fillRect(left, 0, right - left, H);
    ctx.globalAlpha = 1;
    // Dashed boundaries.
    ctx.setLineDash([lw * 2.4, lw * 1.8]);
    ctx.lineWidth = lw * 0.8;
    ctx.strokeStyle = 'rgba(255,255,255,0.9)';
    ctx.beginPath();
    ctx.moveTo(left, 0);
    ctx.lineTo(left, H);
    ctx.moveTo(right, 0);
    ctx.lineTo(right, H);
    ctx.stroke();
    ctx.setLineDash([]);
    // Jump (green) and duck (orange) lines around this player's zone.
    const x0 = Math.max(0, cx - hw * 2.6);
    const x1 = Math.min(W, cx + hw * 2.6);
    ctx.lineWidth = lw * 0.9;
    ctx.strokeStyle = GREEN;
    ctx.beginPath();
    ctx.moveTo(x0, g.jumpLineY * H);
    ctx.lineTo(x1, g.jumpLineY * H);
    ctx.stroke();
    ctx.strokeStyle = ORANGE;
    ctx.beginPath();
    ctx.moveTo(x0, g.duckLineY * H);
    ctx.lineTo(x1, g.duckLineY * H);
    ctx.stroke();
  }

  private drawPerson(ctx: CanvasRenderingContext2D, p: TrackedPerson, W: number, H: number, lw: number, showRing: boolean): void {
    const lm = p.landmarks;
    if (lm.length < 33) return;
    const color = PLAYER_COLORS[p.slot];
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    // Two passes: navy outline, then color.
    for (let pass = 0; pass < 2; pass++) {
      ctx.strokeStyle = pass === 0 ? NAVY : color;
      ctx.lineWidth = pass === 0 ? lw * 2 : lw;
      ctx.beginPath();
      for (let i = 0; i < SKELETON_CONNECTIONS.length; i++) {
        const [a, b] = SKELETON_CONNECTIONS[i];
        const la = lm[a];
        const lb = lm[b];
        if (la.visibility < CAM_MIN_VISIBILITY || lb.visibility < CAM_MIN_VISIBILITY) continue;
        ctx.moveTo(la.x * W, la.y * H);
        ctx.lineTo(lb.x * W, lb.y * H);
      }
      ctx.stroke();
    }
    // Head.
    const nose = lm[LM.NOSE];
    const headR = Math.max(lw * 2, p.scale * W * 0.42);
    if (nose.visibility >= CAM_MIN_VISIBILITY) {
      ctx.lineWidth = lw * 2;
      ctx.strokeStyle = NAVY;
      ctx.beginPath();
      ctx.arc(nose.x * W, nose.y * H, headR, 0, Math.PI * 2);
      ctx.stroke();
      ctx.lineWidth = lw;
      ctx.strokeStyle = color;
      ctx.stroke();
      // Hands-up ring.
      if (showRing && p.handsUpProgress > 0.02) {
        ctx.lineWidth = lw * 1.6;
        ctx.strokeStyle = '#ffd23f';
        ctx.beginPath();
        ctx.arc(nose.x * W, nose.y * H, headR + lw * 2.2, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.min(1, p.handsUpProgress));
        ctx.stroke();
      }
      // Label above head.
      const fs = Math.max(13 * (this.canvas.width / Math.max(1, this.cssW)), headR * 0.75);
      ctx.font = `${fs}px "Lilita One", system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      ctx.lineWidth = fs * 0.22;
      ctx.strokeStyle = NAVY;
      const ty = nose.y * H - headR - lw * 3;
      ctx.strokeText(PLAYER_LABELS[p.slot], nose.x * W, ty);
      ctx.fillStyle = color;
      ctx.fillText(PLAYER_LABELS[p.slot], nose.x * W, ty);
    }
    // Wrist joints.
    for (const j of WRISTS) {
      const l = lm[j];
      if (l.visibility < CAM_MIN_VISIBILITY) continue;
      ctx.fillStyle = NAVY;
      ctx.beginPath();
      ctx.arc(l.x * W, l.y * H, lw * 1.6, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.beginPath();
      ctx.arc(l.x * W, l.y * H, lw * 0.9, 0, Math.PI * 2);
      ctx.fill();
    }
    // Tracking dot (torso anchor).
    const tx = p.center.x * W;
    const ty2 = p.center.y * H;
    ctx.fillStyle = NAVY;
    ctx.beginPath();
    ctx.arc(tx, ty2, lw * 2.1, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = p.confidence > 0.5 ? '#ffd23f' : '#ff4757';
    ctx.beginPath();
    ctx.arc(tx, ty2, lw * 1.3, 0, Math.PI * 2);
    ctx.fill();
  }

  private drawFlash(
    ctx: CanvasRenderingContext2D,
    g: GestureState,
    people: readonly TrackedPerson[],
    W: number,
    H: number,
    now: number,
    big: boolean,
    k: number,
  ): void {
    const f = this.flashes[g.slot];
    const lg = g.lastGesture;
    if (lg && lg.t !== f.t) {
      f.t = lg.t;
      if (lg.kind === 'jump') f.text = 'JUMP!';
      else if (lg.kind === 'duck') f.text = 'SQUAT!';
      else f.text = g.lane < f.prevLane ? '⬅' : g.lane > f.prevLane ? '➡' : g.lane === -1 ? '⬅' : '➡';
    }
    f.prevLane = g.lane;
    if (!lg || f.t < 0) return;
    const age = now - f.t;
    if (age < 0 || age > GESTURE_FLASH_MS) return;
    let person: TrackedPerson | null = null;
    for (let i = 0; i < people.length; i++) if (people[i].slot === g.slot) person = people[i];
    const x = person ? person.center.x * W : g.centerX * W;
    const y = person ? Math.max(H * 0.14, person.center.y * H - person.scale * W * 1.6) : H * 0.2;
    const t = age / GESTURE_FLASH_MS;
    const fs = (big ? 34 : 22) * k * (1 + 0.25 * Math.sin(Math.min(1, t * 3) * Math.PI));
    ctx.globalAlpha = 1 - t * t;
    ctx.font = `${fs}px "Lilita One", system-ui, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.lineJoin = 'round';
    ctx.lineWidth = fs * 0.2;
    ctx.strokeStyle = NAVY;
    ctx.strokeText(f.text, x, y);
    ctx.fillStyle = f.text === 'JUMP!' ? GREEN : f.text === 'SQUAT!' ? ORANGE : '#ffd23f';
    ctx.fillText(f.text, x, y);
    ctx.globalAlpha = 1;
  }

  dispose(): void {
    this.ro?.disconnect();
  }
}

const WRISTS = [LM.LEFT_WRIST, LM.RIGHT_WRIST] as const;
