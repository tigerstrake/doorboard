// Pooled GPU point particles + speed lines. Fixed capacity, zero per-frame allocation.
import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  DynamicDrawUsage,
  LineBasicMaterial,
  LineSegments,
  Mesh,
  PlaneGeometry,
  Points,
  ShaderMaterial,
} from 'three';
import { Color } from 'three';

const VERT = /* glsl */ `
attribute float size;
attribute vec4 tint;
uniform float uScale;
varying vec4 vTint;
void main() {
  vTint = tint;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_PointSize = size * uScale / max(0.1, -mv.z);
  gl_Position = projectionMatrix * mv;
}`;
const FRAG = /* glsl */ `
varying vec4 vTint;
void main() {
  vec2 p = gl_PointCoord - 0.5;
  float d = length(p) * 2.0;
  float a = smoothstep(1.0, 0.25, d);
  if (a * vTint.a < 0.01) discard;
  gl_FragColor = vec4(vTint.rgb, vTint.a * a);
}`;

export class Particles {
  readonly points: Points;
  private readonly mat: ShaderMaterial;
  private readonly geo: BufferGeometry;
  private readonly pos: Float32Array;
  private readonly tint: Float32Array;
  private readonly size: Float32Array;
  private readonly vel: Float32Array;
  private readonly life: Float32Array;
  private readonly maxLife: Float32Array;
  private readonly baseSize: Float32Array;
  private readonly grav: Float32Array;
  private readonly scroll: Float32Array;
  private readonly posAttr: BufferAttribute;
  private readonly tintAttr: BufferAttribute;
  private readonly sizeAttr: BufferAttribute;
  private readonly attrs: readonly BufferAttribute[];
  private cursor = 0;
  /** Index span [lo, hi] that may hold live particles (hi < lo = none): update/upload only that. */
  private lo = 0;
  private hi = -1;
  /** Fraction of requested particles actually spawned (quality). */
  scale = 1;

  constructor(readonly capacity: number) {
    const n = capacity;
    this.pos = new Float32Array(n * 3);
    this.tint = new Float32Array(n * 4);
    this.size = new Float32Array(n);
    this.vel = new Float32Array(n * 3);
    this.life = new Float32Array(n);
    this.maxLife = new Float32Array(n).fill(1);
    this.baseSize = new Float32Array(n);
    this.grav = new Float32Array(n);
    this.scroll = new Float32Array(n);
    this.geo = new BufferGeometry();
    this.posAttr = new BufferAttribute(this.pos, 3).setUsage(DynamicDrawUsage);
    this.tintAttr = new BufferAttribute(this.tint, 4).setUsage(DynamicDrawUsage);
    this.sizeAttr = new BufferAttribute(this.size, 1).setUsage(DynamicDrawUsage);
    this.attrs = [this.posAttr, this.tintAttr, this.sizeAttr];
    this.geo.setAttribute('position', this.posAttr);
    this.geo.setAttribute('tint', this.tintAttr);
    this.geo.setAttribute('size', this.sizeAttr);
    this.mat = new ShaderMaterial({
      vertexShader: VERT,
      fragmentShader: FRAG,
      uniforms: { uScale: { value: 400 } },
      transparent: true,
      depthWrite: false,
      blending: AdditiveBlending,
    });
    this.points = new Points(this.geo, this.mat);
    this.points.frustumCulled = false;
    this.points.renderOrder = 10;
  }

  setScale(pxPerUnitAtOneMeter: number): void {
    this.mat.uniforms.uScale.value = pxPerUnitAtOneMeter;
  }

  /** Burst of n particles around a point. */
  burst(
    n: number,
    x: number, y: number, z: number,
    spread: number, speed: number, up: number,
    color: Color, size: number, life: number, gravity: number, scroll: number,
    rand: () => number,
  ): void {
    const count = Math.max(1, Math.round(n * this.scale));
    for (let k = 0; k < count; k++) {
      const i = this.cursor;
      this.cursor = (this.cursor + 1) % this.capacity;
      if (this.hi < this.lo) this.lo = this.hi = i;
      else if (i < this.lo) this.lo = i;
      else if (i > this.hi) this.hi = i;
      const a = rand() * Math.PI * 2;
      const e = rand() * 2 - 1;
      const sp = speed * (0.4 + rand() * 0.6);
      const h = Math.sqrt(1 - e * e);
      this.pos[i * 3] = x + (rand() - 0.5) * spread;
      this.pos[i * 3 + 1] = y + (rand() - 0.5) * spread;
      this.pos[i * 3 + 2] = z + (rand() - 0.5) * spread;
      this.vel[i * 3] = Math.cos(a) * h * sp;
      this.vel[i * 3 + 1] = e * sp * 0.6 + up * (0.6 + rand() * 0.6);
      this.vel[i * 3 + 2] = Math.sin(a) * h * sp;
      const l = life * (0.6 + rand() * 0.6);
      this.life[i] = l;
      this.maxLife[i] = l;
      this.baseSize[i] = size * (0.6 + rand() * 0.8);
      this.grav[i] = gravity;
      this.scroll[i] = scroll;
      const lum = 0.85 + rand() * 0.3;
      this.tint[i * 4] = Math.min(1, color.r * lum);
      this.tint[i * 4 + 1] = Math.min(1, color.g * lum);
      this.tint[i * 4 + 2] = Math.min(1, color.b * lum);
      this.tint[i * 4 + 3] = 1;
    }
  }

  update(dt: number, worldSpeed: number): void {
    const lo = this.lo;
    const hi = this.hi;
    if (hi < lo) return;
    let newLo = this.capacity;
    let newHi = -1;
    for (let i = lo; i <= hi; i++) {
      if (this.life[i] <= 0) continue;
      const l = (this.life[i] -= dt);
      if (l <= 0) {
        this.size[i] = 0;
        this.tint[i * 4 + 3] = 0;
        continue;
      }
      const drag = 1 - Math.min(1, dt * 1.8);
      this.vel[i * 3] *= drag;
      this.vel[i * 3 + 2] *= drag;
      this.vel[i * 3 + 1] -= this.grav[i] * dt;
      this.pos[i * 3] += this.vel[i * 3] * dt;
      this.pos[i * 3 + 1] += this.vel[i * 3 + 1] * dt;
      this.pos[i * 3 + 2] += (this.vel[i * 3 + 2] + this.scroll[i] * worldSpeed) * dt;
      const f = l / this.maxLife[i];
      this.tint[i * 4 + 3] = f < 0.5 ? f * 2 : 1;
      this.size[i] = this.baseSize[i] * (0.5 + 0.5 * f);
      if (i < newLo) newLo = i;
      newHi = i;
    }
    // Upload the old span: it includes particles that died this frame (zeroed above).
    const n = hi - lo + 1;
    for (const attr of this.attrs) {
      attr.clearUpdateRanges();
      attr.addUpdateRange(lo * attr.itemSize, n * attr.itemSize);
      attr.needsUpdate = true;
    }
    this.lo = newHi < 0 ? 0 : newLo;
    this.hi = newHi;
  }

  clear(): void {
    this.life.fill(0);
    this.size.fill(0);
    this.sizeAttr.clearUpdateRanges();
    this.sizeAttr.needsUpdate = true;
    this.lo = 0;
    this.hi = -1;
  }

  dispose(): void {
    this.geo.dispose();
    this.mat.dispose();
  }
}

/** Streaks rushing past the camera at high speed. */
export class SpeedLines {
  readonly lines: LineSegments;
  private readonly mat: LineBasicMaterial;
  private readonly geo: BufferGeometry;
  private readonly pos: Float32Array;
  private readonly attr: BufferAttribute;
  private readonly seedX: Float32Array;
  private readonly seedY: Float32Array;
  private readonly z: Float32Array;

  constructor(private readonly count: number, rand: () => number) {
    this.pos = new Float32Array(count * 6);
    this.seedX = new Float32Array(count);
    this.seedY = new Float32Array(count);
    this.z = new Float32Array(count);
    for (let i = 0; i < count; i++) this.respawn(i, rand, -rand() * 60);
    this.geo = new BufferGeometry();
    this.attr = new BufferAttribute(this.pos, 3).setUsage(DynamicDrawUsage);
    this.geo.setAttribute('position', this.attr);
    this.mat = new LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0, depthWrite: false, blending: AdditiveBlending, fog: false });
    this.lines = new LineSegments(this.geo, this.mat);
    this.lines.frustumCulled = false;
    this.lines.renderOrder = 11;
  }

  private lenScale = 1;
  private rate = 1;

  /** Tint / streak length / travel rate (slow-mo uses long, slow, blue streaks). */
  setStyle(color: Color, lenScale: number, rate: number): void {
    this.mat.color.copy(color);
    this.lenScale = lenScale;
    this.rate = rate;
  }

  private respawn(i: number, rand: () => number, z: number): void {
    const a = rand() * Math.PI * 2;
    const r = 2.2 + rand() * 4.5;
    this.seedX[i] = Math.cos(a) * r * 1.4;
    this.seedY[i] = Math.sin(a) * r * 0.8 + 1.5;
    this.z[i] = z;
  }

  update(dt: number, speed: number, intensity: number, cx: number, cy: number, cz: number, rand: () => number): void {
    this.lines.visible = intensity > 0.01;
    if (!this.lines.visible) return;
    this.mat.opacity = Math.min(1, 0.55 * intensity);
    const len = (2 + speed * 0.18) * this.lenScale;
    for (let i = 0; i < this.count; i++) {
      this.z[i] += speed * 2.2 * this.rate * dt;
      if (this.z[i] > 2) this.respawn(i, rand, -50 - rand() * 20);
      const x = cx + this.seedX[i];
      const y = cy + this.seedY[i] - 1.5;
      const z = cz + this.z[i];
      const o = i * 6;
      this.pos[o] = x;
      this.pos[o + 1] = y;
      this.pos[o + 2] = z;
      this.pos[o + 3] = x;
      this.pos[o + 4] = y;
      this.pos[o + 5] = z - len;
    }
    this.attr.needsUpdate = true;
  }

  dispose(): void {
    this.geo.dispose();
    this.mat.dispose();
  }
}

const OVERLAY_VERT = /* glsl */ `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = vec4(position.xy, 0.0, 1.0);
}`;
const OVERLAY_FRAG = /* glsl */ `
uniform vec3 uColor;
uniform float uVignette;
uniform float uFill;
uniform float uAspect;
varying vec2 vUv;
void main() {
  vec2 p = (vUv - 0.5) * 2.0;
  p.x *= mix(1.0, uAspect, 0.5);
  float r = length(p);
  float v = smoothstep(0.55, 1.45, r);
  float a = clamp(uVignette * v + uFill, 0.0, 0.85);
  if (a < 0.003) discard;
  gl_FragColor = vec4(uColor, a);
}`;

/** Full-screen tint + vignette quad (one draw call), used for slow-mo / star moods. */
export class ScreenTint {
  readonly mesh: Mesh;
  private readonly mat: ShaderMaterial;
  private readonly geo: PlaneGeometry;
  readonly color = new Color();

  constructor() {
    this.geo = new PlaneGeometry(2, 2);
    this.mat = new ShaderMaterial({
      vertexShader: OVERLAY_VERT,
      fragmentShader: OVERLAY_FRAG,
      uniforms: { uColor: { value: this.color }, uVignette: { value: 0 }, uFill: { value: 0 }, uAspect: { value: 1 } },
      transparent: true,
      depthTest: false,
      depthWrite: false,
    });
    this.mesh = new Mesh(this.geo, this.mat);
    this.mesh.frustumCulled = false;
    this.mesh.renderOrder = 100;
    this.mesh.visible = false;
  }

  set(vignette: number, fill: number, aspect: number): void {
    const u = this.mat.uniforms;
    u.uVignette.value = vignette;
    u.uFill.value = fill;
    u.uAspect.value = aspect;
    this.mesh.visible = vignette > 0.004 || fill > 0.004;
  }

  dispose(): void {
    this.geo.dispose();
    this.mat.dispose();
  }
}
