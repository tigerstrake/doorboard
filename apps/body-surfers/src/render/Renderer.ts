// IRenderer implementation: one WebGLRenderer, one View (scene + camera) per player,
// shared Assets, scissor/viewport split-screen.
import { NeutralToneMapping, PCFShadowMap, SRGBColorSpace, Vector2, WebGLRenderer } from 'three';
import type { IRenderer, PlayerCount, PlayerId, Quality, RenderView } from '../shared/types';
import { Assets } from './Assets';
import { QUALITY } from './config';
import { View } from './View';

/** CSS px gap between split-screen halves. */
const SPLIT_GAP = 4;

export class Renderer implements IRenderer {
  private gl: WebGLRenderer | null = null;
  private assets: Assets | null = null;
  private readonly views: (View | null)[] = [null, null];
  private playerCount: PlayerCount = 1;
  private quality: Quality = 'high';
  private width = 1;
  private height = 1;
  private readonly size = new Vector2();
  private stats = { drawCalls: 0, triangles: 0 };

  init(canvas: HTMLCanvasElement, opts: { quality: Quality }): void {
    const gl = new WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
    gl.outputColorSpace = SRGBColorSpace;
    gl.toneMapping = NeutralToneMapping;
    gl.toneMappingExposure = 1.05;
    gl.shadowMap.type = PCFShadowMap;
    gl.info.autoReset = false;
    gl.setClearColor(0x0b1020, 1);
    this.gl = gl;
    this.assets = new Assets(gl);
    this.quality = opts.quality;
    this.view(0);
    this.applyQuality();
    const w = canvas.clientWidth || window.innerWidth;
    const h = canvas.clientHeight || window.innerHeight;
    this.resize(w, h);
  }

  setPlayerCount(n: PlayerCount): void {
    this.playerCount = n;
    if (n === 2) this.view(1);
  }

  setQuality(q: Quality): void {
    if (q === this.quality) return;
    this.quality = q;
    this.applyQuality();
    this.resize(this.width, this.height);
  }

  render(views: readonly RenderView[], dt: number): void {
    const gl = this.gl;
    if (!gl) return;
    gl.info.reset();
    gl.getSize(this.size);
    const W = this.size.x;
    const H = this.size.y;
    const n = this.playerCount;
    const pr = gl.getPixelRatio();
    gl.setScissorTest(true);
    if (n === 2) {
      gl.setScissor(0, 0, W, H);
      gl.setViewport(0, 0, W, H);
      gl.clear();
    }
    const vw = n === 2 ? Math.floor((W - SPLIT_GAP) / 2) : W;
    const step = dt > 0 ? Math.min(dt, 0.1) : 0;
    for (let i = 0; i < n; i++) {
      const x = i === 0 ? 0 : W - vw;
      gl.setViewport(x, 0, vw, H);
      gl.setScissor(x, 0, vw, H);
      const rv = views[i];
      if (!rv) {
        gl.clear();
        continue;
      }
      const v = this.view(i as PlayerId);
      v.update(rv.snapshot, rv.events, step, vw / H, H * pr, rv.mimic ?? null);
      gl.render(v.scene, v.camera);
    }
    gl.setScissorTest(false);
    this.stats.drawCalls = gl.info.render.calls;
    this.stats.triangles = gl.info.render.triangles;
  }

  resize(width: number, height: number): void {
    this.width = Math.max(1, width);
    this.height = Math.max(1, height);
    const gl = this.gl;
    if (!gl) return;
    gl.setPixelRatio(Math.min(window.devicePixelRatio || 1, QUALITY[this.quality].maxPixelRatio));
    gl.setSize(this.width, this.height, false);
  }

  getStats(): { drawCalls: number; triangles: number } {
    return this.stats;
  }

  dispose(): void {
    for (let i = 0; i < this.views.length; i++) {
      this.views[i]?.dispose();
      this.views[i] = null;
    }
    this.assets?.dispose();
    this.assets = null;
    this.gl?.dispose();
    this.gl = null;
  }

  private view(i: PlayerId): View {
    let v = this.views[i];
    if (!v) {
      if (!this.assets) throw new Error('Renderer not initialized');
      v = new View(this.assets, i);
      v.setQuality(this.quality);
      this.views[i] = v;
    }
    return v;
  }

  private applyQuality(): void {
    const gl = this.gl;
    if (!gl) return;
    const q = QUALITY[this.quality];
    if (gl.shadowMap.enabled !== q.shadows) {
      gl.shadowMap.enabled = q.shadows;
      this.assets?.invalidate();
    }
    for (const v of this.views) v?.setQuality(this.quality);
  }
}
