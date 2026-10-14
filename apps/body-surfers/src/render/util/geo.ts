// Build-time geometry helpers: merge primitives into one vertex-colored geometry,
// boxes with per-face atlas UVs, and profile extrusions. Allocation here is fine
// (runs once at startup); nothing in this file is used per frame.
import {
  BoxGeometry,
  BufferAttribute,
  BufferGeometry,
  Color,
  Euler,
  Float32BufferAttribute,
  Matrix4,
  Quaternion,
  ShapeUtils,
  Vector2,
  Vector3,
} from 'three';
import type { ColorRepresentation } from 'three';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';

/** u0, v0, u1, v1 */
export type UVRect = readonly [number, number, number, number];
export type UVPoint = readonly [number, number];
export type UVSpec = UVRect | UVPoint;

const _e = new Euler();
const _q = new Quaternion();
const _p = new Vector3();
const _s = new Vector3();
const _c = new Color();

/** Compose a transform: translation, Euler XYZ rotation (radians), scale. */
export function mat4(
  px = 0, py = 0, pz = 0,
  rx = 0, ry = 0, rz = 0,
  sx = 1, sy = 1, sz = 1,
): Matrix4 {
  _e.set(rx, ry, rz);
  _q.setFromEuler(_e);
  _p.set(px, py, pz);
  _s.set(sx, sy, sz);
  return new Matrix4().compose(_p, _q, _s);
}

export type ShadeFn = (x: number, y: number, z: number, nx: number, ny: number, nz: number, out: Color) => void;

export interface PartOptions {
  color?: ColorRepresentation;
  matrix?: Matrix4;
  /** Remap the part's 0..1 uvs into a rect, or collapse them onto a point (solid swatch). */
  uv?: UVSpec;
  /** Recompute flat (faceted) normals. */
  flat?: boolean;
  /** Per-vertex color override, evaluated after the matrix is applied. Receives the base color in `out`. */
  shade?: ShadeFn;
}

export function remapUVs(g: BufferGeometry, spec: UVSpec, start = 0, count = -1): void {
  const uv = g.getAttribute('uv') as BufferAttribute;
  const end = count < 0 ? uv.count : start + count;
  for (let i = start; i < end; i++) {
    if (spec.length === 2) {
      uv.setXY(i, spec[0], spec[1]);
    } else {
      const u = uv.getX(i);
      const v = uv.getY(i);
      uv.setXY(i, spec[0] + (spec[2] - spec[0]) * u, spec[1] + (spec[3] - spec[1]) * v);
    }
  }
  uv.needsUpdate = true;
}

/** Collects parts (taking ownership) and merges them into one non-indexed geometry with position/normal/uv/color. */
export class GeoBuilder {
  private readonly parts: BufferGeometry[] = [];

  add(src: BufferGeometry, o: PartOptions = {}): this {
    const g = src.index ? src.toNonIndexed() : src.clone();
    src.dispose();
    if (o.matrix) g.applyMatrix4(o.matrix);
    if (o.flat || !g.getAttribute('normal')) g.computeVertexNormals();
    const count = g.getAttribute('position').count;
    if (!g.getAttribute('uv')) g.setAttribute('uv', new Float32BufferAttribute(new Float32Array(count * 2), 2));
    if (o.uv) remapUVs(g, o.uv);
    const pos = g.getAttribute('position');
    const nor = g.getAttribute('normal');
    const col = new Float32Array(count * 3);
    const base = new Color(o.color ?? 0xffffff);
    for (let i = 0; i < count; i++) {
      _c.copy(base);
      if (o.shade) o.shade(pos.getX(i), pos.getY(i), pos.getZ(i), nor.getX(i), nor.getY(i), nor.getZ(i), _c);
      col[i * 3] = _c.r;
      col[i * 3 + 1] = _c.g;
      col[i * 3 + 2] = _c.b;
    }
    g.setAttribute('color', new BufferAttribute(col, 3));
    for (const name of Object.keys(g.attributes)) {
      if (name !== 'position' && name !== 'normal' && name !== 'uv' && name !== 'color') g.deleteAttribute(name);
    }
    g.morphAttributes = {};
    g.clearGroups();
    this.parts.push(g);
    return this;
  }

  get empty(): boolean {
    return this.parts.length === 0;
  }

  build(): BufferGeometry {
    const merged = mergeGeometries(this.parts, false) as BufferGeometry | null;
    if (!merged) throw new Error('GeoBuilder: merge failed');
    for (const p of this.parts) p.dispose();
    this.parts.length = 0;
    merged.computeBoundingSphere();
    merged.computeBoundingBox();
    return merged;
  }
}

export type BoxFace = 'px' | 'nx' | 'py' | 'ny' | 'pz' | 'nz';
const FACE_ORDER: readonly BoxFace[] = ['px', 'nx', 'py', 'ny', 'pz', 'nz'];

/** Box with optional per-face uv rects/points (faces not listed keep `fallback`, or 0..1). */
export function boxGeo(
  w: number, h: number, d: number,
  faces: Partial<Record<BoxFace, UVSpec>> = {},
  fallback?: UVSpec,
): BufferGeometry {
  const g = new BoxGeometry(w, h, d);
  for (let f = 0; f < 6; f++) {
    const spec = faces[FACE_ORDER[f]] ?? fallback;
    if (spec) remapUVs(g, spec, f * 4, 4);
  }
  return g;
}

/**
 * Extrude an open 2D polyline profile (x,y) along -z from z0 to z1 (z1 < z0).
 * Side uvs: u = 0..1 along the length, v = cumulative perimeter fraction mapped through vMap.
 * Optional end caps close the polygon (profile + straight closing edge); capUV maps (x,y) → uv.
 */
export function extrudeProfile(
  profile: readonly (readonly [number, number])[],
  z0: number,
  z1: number,
  opts: {
    vMap?: (f: number) => number;
    uMap?: (f: number) => number;
    caps?: boolean;
    capUV?: (x: number, y: number, front: boolean) => [number, number];
  } = {},
): BufferGeometry {
  const vMap = opts.vMap ?? ((f: number) => f);
  const uMap = opts.uMap ?? ((f: number) => f);
  const pos: number[] = [];
  const nor: number[] = [];
  const uv: number[] = [];
  let total = 0;
  for (let i = 0; i + 1 < profile.length; i++) {
    total += Math.hypot(profile[i + 1][0] - profile[i][0], profile[i + 1][1] - profile[i][1]);
  }
  let acc = 0;
  for (let i = 0; i + 1 < profile.length; i++) {
    const [ax, ay] = profile[i];
    const [bx, by] = profile[i + 1];
    const len = Math.hypot(bx - ax, by - ay);
    const nx = -(by - ay) / len;
    const ny = (bx - ax) / len;
    const fa = acc / total;
    acc += len;
    const fb = acc / total;
    const va = vMap(fa);
    const vb = vMap(fb);
    const u0 = uMap(0);
    const u1 = uMap(1);
    // A(a,z0) B(b,z0) C(b,z1) D(a,z1): triangles ABC, ACD (CCW seen from outside)
    const quad: [number, number, number, number, number][] = [
      [ax, ay, z0, u0, va],
      [bx, by, z0, u0, vb],
      [bx, by, z1, u1, vb],
      [ax, ay, z0, u0, va],
      [bx, by, z1, u1, vb],
      [ax, ay, z1, u1, va],
    ];
    for (const [x, y, z, u, v] of quad) {
      pos.push(x, y, z);
      nor.push(nx, ny, 0);
      uv.push(u, v);
    }
  }
  if (opts.caps) {
    const contour = profile.map(([x, y]) => new Vector2(x, y));
    const tris = ShapeUtils.triangulateShape(contour, []);
    for (const front of [true, false]) {
      const z = front ? z0 : z1;
      for (const t of tris) {
        // front cap faces +z: needs CCW in xy; back cap faces -z: needs CW.
        const a = contour[t[0]];
        const b = contour[t[1]];
        const c = contour[t[2]];
        const cross = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
        const order = (cross > 0) === front ? [t[0], t[1], t[2]] : [t[0], t[2], t[1]];
        for (const idx of order) {
          const p = contour[idx];
          pos.push(p.x, p.y, z);
          nor.push(0, 0, front ? 1 : -1);
          const c = opts.capUV ? opts.capUV(p.x, p.y, front) : [0, 0];
          uv.push(c[0], c[1]);
        }
      }
    }
  }
  const g = new BufferGeometry();
  g.setAttribute('position', new Float32BufferAttribute(pos, 3));
  g.setAttribute('normal', new Float32BufferAttribute(nor, 3));
  g.setAttribute('uv', new Float32BufferAttribute(uv, 2));
  return g;
}

/** A flat quad in the XZ plane facing +y, centered at origin (w along x, d along z). */
export function groundQuad(w: number, d: number, uv: UVRect = [0, 0, 1, 1]): BufferGeometry {
  const hw = w / 2;
  const hd = d / 2;
  const g = new BufferGeometry();
  // two triangles, CCW seen from +y
  const p = [-hw, 0, hd, hw, 0, hd, hw, 0, -hd, -hw, 0, hd, hw, 0, -hd, -hw, 0, -hd];
  const [u0, v0, u1, v1] = uv;
  const t = [u0, v0, u1, v0, u1, v1, u0, v0, u1, v1, u0, v1];
  g.setAttribute('position', new Float32BufferAttribute(p, 3));
  g.setAttribute('normal', new Float32BufferAttribute([0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0], 3));
  g.setAttribute('uv', new Float32BufferAttribute(t, 2));
  return g;
}

/** Convert a canvas pixel rect into a uv rect (flipY texture convention). */
export function pxRect(x: number, y: number, w: number, h: number, cw: number, ch: number): UVRect {
  return [x / cw, 1 - (y + h) / ch, (x + w) / cw, 1 - y / ch];
}

/** Center of a canvas pixel rect as a uv point. */
export function pxPoint(x: number, y: number, cw: number, ch: number): UVPoint {
  return [x / cw, 1 - y / ch];
}
