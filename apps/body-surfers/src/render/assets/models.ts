// Procedural geometry (built once at init, shared by all views).
import {
  BoxGeometry,
  BufferGeometry,
  CircleGeometry,
  Color,
  ConeGeometry,
  CylinderGeometry,
  ExtrudeGeometry,
  Float32BufferAttribute,
  IcosahedronGeometry,
  PlaneGeometry,
  Shape,
  SphereGeometry,
  TorusGeometry,
} from 'three';
import {
  BARRIER_HIGH_CLEARANCE,
  BARRIER_HIGH_DEPTH,
  BARRIER_HIGH_TOP,
  BARRIER_LOW_DEPTH,
  BARRIER_LOW_HEIGHT,
  BARRIER_WIDTH,
  COIN_RADIUS,
  LANE_WIDTH,
  RAMP_LENGTH,
  RAMP_WIDTH,
  TRAIN_CAR_LENGTH,
} from '../../shared/constants';
import { COLORS, ENV_AHEAD, ENV_BEHIND, LAYOUT, PASTELS, PERIOD, SEGMENT_LENGTH } from '../config';
import { GeoBuilder, boxGeo, extrudeProfile, groundQuad, mat4, pxPoint, pxRect } from '../util/geo';
import type { ShadeFn, UVPoint, UVRect } from '../util/geo';
import { HashRng } from '../util/rand';
import { CAR, CAR_PROFILE, frontUV, liverySwatch, sideV } from './trainSpec';
import { FACADE_PLAIN, FACADE_TILE, GRAVEL_TILE, PROPS, WALL_BANDS } from './textures';

const PI = Math.PI;
const prect = (r: readonly [number, number, number, number]): UVRect => pxRect(r[0], r[1], r[2], r[3], PROPS.size, PROPS.size);
const PWHITE: UVPoint = pxPoint(64, 320, PROPS.size, PROPS.size);

/** Darken faces pointing down / sides slightly for a baked cartoon look. */
const bakeShade: ShadeFn = (_x, _y, _z, _nx, ny, _nz, out) => {
  if (ny < -0.5) out.multiplyScalar(0.6);
};

// ---------------------------------------------------------------------------
// Trains
// ---------------------------------------------------------------------------
export function buildTrain(cars: number): BufferGeometry {
  const b = new GeoBuilder();
  const hw = CAR.halfWidth;
  for (let i = 0; i < cars; i++) {
    const z0 = -(i * TRAIN_CAR_LENGTH + CAR.inset);
    const z1 = z0 - CAR.bodyLength;
    const zc = (z0 + z1) / 2;
    b.add(
      extrudeProfile(CAR_PROFILE, z0, z1, {
        vMap: sideV,
        caps: true,
        capUV: (x, y) => frontUV(x, y),
      }),
    );
    // underframe
    b.add(boxGeo(hw * 2 - 0.3, 0.42, CAR.bodyLength - 0.6), { matrix: mat4(0, 0.36, zc), uv: liverySwatch('under') });
    // skirt stripe
    b.add(boxGeo(hw * 2 + 0.02, 0.06, CAR.bodyLength), { matrix: mat4(0, CAR.bodyBottom + 0.02, zc), uv: liverySwatch('bodyDark') });
    // bogies + wheels
    for (const bz of [z0 - 2.4, z1 + 2.4]) {
      b.add(boxGeo(hw * 2 - 0.5, 0.22, 2.4), { matrix: mat4(0, 0.42, bz), uv: liverySwatch('black') });
      for (const wz of [-0.75, 0.75]) {
        for (const sx of [-1, 1]) {
          b.add(new CylinderGeometry(0.3, 0.3, 0.12, 12), {
            matrix: mat4(sx * 0.82, 0.32, bz + wz, 0, 0, PI / 2),
            uv: liverySwatch('rubber'),
          });
        }
      }
    }
    // roof units
    b.add(boxGeo(1.1, 0.12, 2.2), { matrix: mat4(0, CAR.roof + 0.04, zc - 3), uv: liverySwatch('chrome') });
    b.add(boxGeo(1.1, 0.12, 2.2), { matrix: mat4(0, CAR.roof + 0.04, zc + 3), uv: liverySwatch('chrome') });
    // bumper
    b.add(boxGeo(hw * 2 - 0.2, 0.18, 0.14), { matrix: mat4(0, 0.7, z0 + 0.04), uv: liverySwatch('black') });
    if (i > 0) {
      // coupler / gangway between cars
      b.add(boxGeo(1.4, 2.2, CAR.inset * 2 + 0.1), { matrix: mat4(0, 1.75, z0 + CAR.inset), uv: liverySwatch('rubber') });
    }
  }
  return b.build();
}

/** Two headlight glows + a light pool on the ground in front (local train space). */
export function buildHeadlightGlow(): BufferGeometry {
  const b = new GeoBuilder();
  const hw = CAR.halfWidth;
  for (const sx of [-1, 1]) {
    b.add(new PlaneGeometry(1.2, 1.2), { matrix: mat4(sx * (hw - 0.42), 1.45, 0.06), color: '#fff3c0' });
  }
  b.add(new PlaneGeometry(1.0, 1.0), { matrix: mat4(0, 2.95, 0.06), color: '#ffffff' });
  b.add(groundQuad(3.4, 10), { matrix: mat4(0, 0.31, 5), color: '#a08a50' });
  return b.build();
}

// ---------------------------------------------------------------------------
// Barriers / ramp (vertex colors × props atlas). Centered at z = 0 (depth axis).
// ---------------------------------------------------------------------------
export function buildBarrierLow(): BufferGeometry {
  const b = new GeoBuilder();
  const hw = BARRIER_WIDTH / 2;
  const red = prect(PROPS.redStripes);
  for (const sx of [-1, 1]) {
    b.add(boxGeo(0.12, BARRIER_LOW_HEIGHT, 0.12), { matrix: mat4(sx * (hw - 0.1), BARRIER_LOW_HEIGHT / 2, 0), uv: PWHITE, color: '#dfe3e8' });
    b.add(boxGeo(0.16, 0.08, 0.7), { matrix: mat4(sx * (hw - 0.1), 0.04, 0), uv: PWHITE, color: '#e8323e' });
  }
  b.add(boxGeo(BARRIER_WIDTH, 0.44, BARRIER_LOW_DEPTH * 0.3, { pz: red, nz: red }, PWHITE), {
    matrix: mat4(0, BARRIER_LOW_HEIGHT - 0.24, 0),
    color: '#ffffff',
    shade: (_x, _y, _z, _nx, ny, nz, out) => {
      if (Math.abs(nz) < 0.5) out.set(ny > 0.5 ? '#f2f2f2' : '#c22a33');
    },
  });
  b.add(boxGeo(BARRIER_WIDTH - 0.2, 0.08, 0.08), { matrix: mat4(0, 0.42, 0), uv: PWHITE, color: '#e8323e' });
  // small warning light
  b.add(new SphereGeometry(0.07, 8, 6), { matrix: mat4(-hw + 0.1, BARRIER_LOW_HEIGHT + 0.05, 0), uv: PWHITE, color: '#ffb31a' });
  return b.build();
}

export function buildBarrierHigh(): BufferGeometry {
  const b = new GeoBuilder();
  const hw = BARRIER_WIDTH / 2;
  const yel = prect(PROPS.yellowStripes);
  const panelH = BARRIER_HIGH_TOP - BARRIER_HIGH_CLEARANCE - 0.05;
  for (const sx of [-1, 1]) {
    b.add(boxGeo(0.16, BARRIER_HIGH_TOP + 0.1, 0.16), { matrix: mat4(sx * (hw + 0.02), (BARRIER_HIGH_TOP + 0.1) / 2, 0), uv: PWHITE, color: '#3a3f48' });
    b.add(boxGeo(0.3, 0.1, 0.8), { matrix: mat4(sx * (hw + 0.02), 0.05, 0), uv: PWHITE, color: '#3a3f48' });
    b.add(new SphereGeometry(0.1, 8, 6), { matrix: mat4(sx * (hw + 0.02), BARRIER_HIGH_TOP + 0.18, 0), uv: PWHITE, color: '#ff3b2f' });
  }
  b.add(boxGeo(BARRIER_WIDTH, panelH, BARRIER_HIGH_DEPTH * 0.4, { pz: yel, nz: yel }, PWHITE), {
    matrix: mat4(0, BARRIER_HIGH_CLEARANCE + 0.05 + panelH / 2, 0),
    shade: (_x, _y, _z, _nx, _ny, nz, out) => {
      if (Math.abs(nz) < 0.5) out.set('#1c1a22');
    },
  });
  b.add(boxGeo(BARRIER_WIDTH + 0.1, 0.1, BARRIER_HIGH_DEPTH * 0.6), { matrix: mat4(0, BARRIER_HIGH_CLEARANCE + 0.05, 0), uv: PWHITE, color: '#1c1a22' });
  b.add(boxGeo(BARRIER_WIDTH + 0.1, 0.1, BARRIER_HIGH_DEPTH * 0.6), { matrix: mat4(0, BARRIER_HIGH_TOP - 0.02, 0), uv: PWHITE, color: '#1c1a22' });
  return b.build();
}

/** Ramp from z = 0 (height 0) to z = -RAMP_LENGTH (height = rampTop). */
export function buildRamp(rampTop: number): BufferGeometry {
  const b = new GeoBuilder();
  const hw = RAMP_WIDTH / 2;
  const L = RAMP_LENGTH;
  const H = rampTop;
  const top = new BufferGeometry();
  const ch = prect(PROPS.chevrons);
  // top quad (two tris, CCW from above/front)
  top.setAttribute('position', new Float32BufferAttribute([
    -hw, 0.02, 0, hw, 0.02, 0, hw, H, -L,
    -hw, 0.02, 0, hw, H, -L, -hw, H, -L,
  ], 3));
  top.setAttribute('uv', new Float32BufferAttribute([
    ch[0], ch[1], ch[2], ch[1], ch[2], ch[3],
    ch[0], ch[1], ch[2], ch[3], ch[0], ch[3],
  ], 2));
  b.add(top, { flat: true });
  for (const sx of [-1, 1]) {
    const side = new BufferGeometry();
    const x = sx * hw;
    const p = sx > 0 ? [x, 0, 0, x, 0, -L, x, H, -L] : [x, 0, 0, x, H, -L, x, 0, -L];
    side.setAttribute('position', new Float32BufferAttribute(p, 3));
    b.add(side, { flat: true, uv: PWHITE, color: '#e0a516' });
    // edge rails
    const len = Math.hypot(L, H);
    b.add(boxGeo(0.1, 0.12, len), {
      matrix: mat4(x, H / 2 + 0.06, -L / 2, Math.atan2(H, L), 0, 0),
      uv: PWHITE,
      color: '#1c1a22',
    });
    // support legs
    for (const f of [0.35, 0.7, 0.98]) {
      const h = H * f;
      b.add(boxGeo(0.12, h, 0.12), { matrix: mat4(x * 0.92, h / 2, -L * f), uv: PWHITE, color: '#7a6a3a' });
    }
  }
  return b.build();
}

// ---------------------------------------------------------------------------
// Collectibles (centered at origin)
// ---------------------------------------------------------------------------
export function buildCoin(): BufferGeometry {
  const b = new GeoBuilder();
  const face = prect(PROPS.coinFace);
  const r = COIN_RADIUS;
  b.add(new CylinderGeometry(r, r, 0.1, 20, 1, true), { matrix: mat4(0, 0, 0, PI / 2), uv: PWHITE, color: '#e8a41a' });
  b.add(new CircleGeometry(r, 20), { matrix: mat4(0, 0, 0.05), uv: face });
  b.add(new CircleGeometry(r, 20), { matrix: mat4(0, 0, -0.05, 0, PI), uv: face });
  return b.build();
}

export function buildMagnet(): BufferGeometry {
  const b = new GeoBuilder();
  b.add(new TorusGeometry(0.3, 0.11, 8, 18, PI), { uv: PWHITE, color: '#e8323e' });
  for (const sx of [-1, 1]) {
    b.add(new CylinderGeometry(0.11, 0.11, 0.18, 10), { matrix: mat4(sx * 0.3, -0.09, 0), uv: PWHITE, color: '#e8323e' });
    b.add(new CylinderGeometry(0.112, 0.112, 0.14, 10), { matrix: mat4(sx * 0.3, -0.25, 0), uv: PWHITE, color: '#e6ecf2' });
  }
  return b.build();
}

export function buildBadge(): BufferGeometry {
  const b = new GeoBuilder();
  const face = prect(PROPS.badge2x);
  const r = 0.42;
  b.add(new CylinderGeometry(r, r, 0.12, 24, 1, true), { matrix: mat4(0, 0, 0, PI / 2), uv: PWHITE, color: '#7b2fe0' });
  // inner circle of the atlas cell; circle uvs span the full cell
  b.add(new CircleGeometry(r, 24), { matrix: mat4(0, 0, 0.06), uv: face });
  b.add(new CircleGeometry(r, 24), { matrix: mat4(0, 0, -0.06, 0, PI), uv: face });
  return b.build();
}

export function buildSneaker(): BufferGeometry {
  const b = new GeoBuilder();
  b.add(boxGeo(0.24, 0.09, 0.6), { matrix: mat4(0, -0.16, 0), uv: PWHITE, color: '#ffffff' });
  b.add(boxGeo(0.22, 0.22, 0.42), { matrix: mat4(0, 0.0, 0.06), uv: PWHITE, color: '#2fd36a' });
  b.add(new SphereGeometry(0.13, 10, 8), { matrix: mat4(0, -0.06, -0.18, 0, 0, 0, 0.85, 0.75, 1.2), uv: PWHITE, color: '#2fd36a' });
  b.add(boxGeo(0.2, 0.16, 0.12), { matrix: mat4(0, 0.17, 0.18), uv: PWHITE, color: '#2fd36a' });
  for (let i = 0; i < 3; i++) b.add(boxGeo(0.23, 0.025, 0.04), { matrix: mat4(0, 0.1, -0.02 + i * 0.08), uv: PWHITE, color: '#ffffff' });
  for (const sx of [-1, 1]) {
    b.add(boxGeo(0.02, 0.18, 0.32), { matrix: mat4(sx * 0.13, 0.1, 0.18, 0.5, 0, 0), uv: PWHITE, color: '#ffe14d' });
  }
  return b.build();
}

/** Hoverboard deck along z (length 1.25), top at y = 0.07. */
export function buildBoard(): BufferGeometry {
  const b = new GeoBuilder();
  const deck = prect(PROPS.board);
  b.add(boxGeo(0.42, 0.06, 1.0, { py: deck, ny: deck }, PWHITE), { matrix: mat4(0, 0.04, 0), color: '#ffffff', shade: (_x, _y, _z, _nx, ny, _nz, out) => { if (Math.abs(ny) < 0.5) out.set('#1a8fd0'); } });
  for (const sz of [-1, 1]) {
    b.add(new CylinderGeometry(0.21, 0.21, 0.06, 14, 1, false, sz > 0 ? -PI / 2 : PI / 2, PI), { matrix: mat4(0, 0.04, sz * 0.5), uv: PWHITE, color: '#28e0ff' });
  }
  b.add(boxGeo(0.3, 0.04, 0.6), { matrix: mat4(0, -0.01, 0), uv: PWHITE, color: '#ff3d7f' });
  return b.build();
}

/** Twin-tank jetpack. Origin = back-plate front face (attaches to the torso back); tanks extend toward +z. */
export function buildJetpack(): BufferGeometry {
  const b = new GeoBuilder();
  b.add(boxGeo(0.34, 0.42, 0.06), { matrix: mat4(0, 0, 0.03), uv: PWHITE, color: '#6b7685' });
  for (const sx of [-1, 1]) {
    const x = sx * 0.12;
    b.add(new CylinderGeometry(0.105, 0.105, 0.46, 14), { matrix: mat4(x, 0, 0.16), uv: PWHITE, color: '#ff8a1f', shade: bakeShade });
    b.add(new SphereGeometry(0.105, 14, 7, 0, PI * 2, 0, PI / 2), { matrix: mat4(x, 0.23, 0.16), uv: PWHITE, color: '#ffb35c' });
    b.add(new CylinderGeometry(0.108, 0.108, 0.05, 14), { matrix: mat4(x, 0.1, 0.16), uv: PWHITE, color: '#ffffff' });
    b.add(new CylinderGeometry(0.06, 0.09, 0.1, 12, 1, true), { matrix: mat4(x, -0.28, 0.16), uv: PWHITE, color: '#5c6672' });
  }
  b.add(boxGeo(0.06, 0.1, 0.1), { matrix: mat4(0, 0.14, 0.2), uv: PWHITE, color: '#e8323e' });
  for (const sx of [-1, 1]) b.add(boxGeo(0.05, 0.4, 0.04), { matrix: mat4(sx * 0.15, 0.02, -0.01), uv: PWHITE, color: '#2d333b' });
  return b.build();
}

/** Flame cone: base at origin, tip at y = -1 (scale y for length). Vertex-colored hot core to cool tip. */
export function buildFlame(): BufferGeometry {
  const b = new GeoBuilder();
  const grad = (inner: string, outer: string): ShadeFn => (_x, y, _z, _nx, _ny, _nz, out) => {
    const k = Math.min(1, Math.max(0, -y));
    out.set(inner).lerp(new Color(outer), k);
  };
  b.add(new ConeGeometry(0.1, 1, 10, 1, true), { matrix: mat4(0, -0.5, 0, PI), shade: grad('#ffd27a', '#ff3d00') });
  b.add(new ConeGeometry(0.05, 0.6, 8, 1, true), { matrix: mat4(0, -0.3, 0, PI), shade: grad('#ffffff', '#ffb000') });
  return b.build();
}

/** Hourglass inside a blue frame (slow-mo). */
export function buildHourglass(): BufferGeometry {
  const b = new GeoBuilder();
  const frame = '#2a6fd6';
  for (const sy of [-1, 1]) {
    b.add(new CylinderGeometry(0.3, 0.3, 0.07, 20), { matrix: mat4(0, sy * 0.36, 0), uv: PWHITE, color: frame });
    b.add(new CylinderGeometry(sy > 0 ? 0.24 : 0.03, sy > 0 ? 0.03 : 0.24, 0.32, 16), { matrix: mat4(0, sy * 0.17, 0), uv: PWHITE, color: '#bfe6ff' });
  }
  // sand: pile at the bottom, a little left on top, and a falling stream
  b.add(new ConeGeometry(0.17, 0.14, 14), { matrix: mat4(0, -0.26, 0), uv: PWHITE, color: '#ffd23f' });
  b.add(new ConeGeometry(0.1, 0.1, 12), { matrix: mat4(0, 0.1, 0, PI), uv: PWHITE, color: '#ffd23f' });
  b.add(new CylinderGeometry(0.015, 0.015, 0.3, 5), { matrix: mat4(0, -0.08, 0), uv: PWHITE, color: '#ffe680' });
  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * PI * 2 + 0.5;
    b.add(new CylinderGeometry(0.028, 0.028, 0.72, 6), { matrix: mat4(Math.cos(a) * 0.26, 0, Math.sin(a) * 0.26), uv: PWHITE, color: frame });
  }
  return b.build();
}

/** Chunky 5-point star, beveled, facing +z. */
export function buildStar(r = 0.55, depth = 0.2): BufferGeometry {
  const shape = new Shape();
  for (let i = 0; i < 10; i++) {
    const a = PI / 2 + (i / 10) * PI * 2;
    const rr = i % 2 === 0 ? r : r * 0.46;
    const x = Math.cos(a) * rr;
    const y = Math.sin(a) * rr;
    if (i === 0) shape.moveTo(x, y);
    else shape.lineTo(x, y);
  }
  shape.closePath();
  const geo = new ExtrudeGeometry(shape, { depth, bevelEnabled: true, bevelThickness: 0.06, bevelSize: 0.05, bevelSegments: 1, curveSegments: 1 });
  const b = new GeoBuilder();
  b.add(geo, {
    matrix: mat4(0, 0, -depth / 2),
    uv: PWHITE,
    flat: true,
    color: '#ffd23f',
    shade: (_x, _y, _z, _nx, _ny, nz, out) => {
      if (Math.abs(nz) < 0.6) out.set('#f09a00');
      else if (Math.abs(nz) < 0.98) out.set('#ffe680');
    },
  });
  return b.build();
}

/** Puffy cloud with coins dripping from it (coin rain). */
export function buildCoinCloud(): BufferGeometry {
  const b = new GeoBuilder();
  const puffs: readonly (readonly [number, number, number, number])[] = [
    [0, 0.22, 0, 0.24], [-0.24, 0.14, 0, 0.18], [0.24, 0.14, 0, 0.19], [-0.1, 0.32, 0.02, 0.17], [0.13, 0.3, -0.02, 0.16],
  ];
  for (const [x, y, z, r] of puffs) {
    b.add(new IcosahedronGeometry(r, 1), { matrix: mat4(x, y, z), uv: PWHITE, flat: true, color: '#ffffff', shade: (_x, _y, _z, _nx, ny, _nz, out) => { if (ny < -0.3) out.set('#c9dcf0'); } });
  }
  const cr = 0.11;
  for (const [x, y, rz] of [[-0.2, -0.12, 0.3], [0.05, -0.28, -0.2], [0.25, -0.1, 0.5], [-0.05, -0.5, 0.1]] as const) {
    b.add(new CylinderGeometry(cr, cr, 0.05, 14), { matrix: mat4(x, y, 0, PI / 2, 0, rz), uv: PWHITE, color: '#ffc62e', shade: (_x, _y, _z, _nx, _ny, nz, out) => { if (Math.abs(nz) < 0.5) out.set('#e09a12'); } });
  }
  return b.build();
}

export function buildHalo(): BufferGeometry {
  return new PlaneGeometry(1.9, 1.9);
}

// ---------------------------------------------------------------------------
// Runner parts (vertex colors, per player). Origin = joint pivot.
// ---------------------------------------------------------------------------
export interface RunnerGeos {
  pelvis: BufferGeometry;
  torso: BufferGeometry;
  head: BufferGeometry;
  upperArm: BufferGeometry;
  forearm: BufferGeometry;
  thigh: BufferGeometry;
  shin: BufferGeometry;
}

export const RIG = {
  hipY: 0.82,
  torsoY: 0.04,
  neckY: 0.44,
  shoulderX: 0.27,
  shoulderY: 0.39,
  upperArm: 0.27,
  hipX: 0.1,
  thigh: 0.38,
} as const;

export function buildRunner(hoodie: string, cap: string): RunnerGeos {
  const hood = new Color(hoodie).multiplyScalar(0.78).getHexString();
  const hoodDark = `#${hood}`;
  const skin = COLORS.skin;
  const jeans = COLORS.jeans;
  const g = (f: (b: GeoBuilder) => void): BufferGeometry => {
    const b = new GeoBuilder();
    f(b);
    return b.build();
  };
  return {
    pelvis: g((b) => {
      b.add(boxGeo(0.36, 0.2, 0.24), { matrix: mat4(0, 0, 0), color: jeans, shade: bakeShade });
      b.add(boxGeo(0.37, 0.05, 0.25), { matrix: mat4(0, 0.08, 0), color: '#3a2a20' });
    }),
    torso: g((b) => {
      b.add(boxGeo(0.46, 0.46, 0.28), { matrix: mat4(0, 0.22, 0), color: hoodie, shade: bakeShade });
      b.add(boxGeo(0.4, 0.06, 0.26), { matrix: mat4(0, 0.0, 0), color: hoodDark });
      // hood behind neck
      b.add(new SphereGeometry(0.17, 10, 8), { matrix: mat4(0, 0.43, 0.1, 0, 0, 0, 1.2, 0.7, 0.8), color: hoodDark });
      // pocket + strings (front = -z)
      b.add(boxGeo(0.3, 0.12, 0.02), { matrix: mat4(0, 0.1, -0.145), color: hoodDark });
      b.add(boxGeo(0.02, 0.14, 0.02), { matrix: mat4(-0.06, 0.34, -0.15), color: '#ffffff' });
      b.add(boxGeo(0.02, 0.14, 0.02), { matrix: mat4(0.06, 0.34, -0.15), color: '#ffffff' });
      // back logo (faces the camera)
      b.add(new CircleGeometry(0.1, 14), { matrix: mat4(0, 0.25, 0.142), color: '#ffffff' });
      b.add(new CircleGeometry(0.06, 5), { matrix: mat4(0, 0.25, 0.144), color: cap });
    }),
    head: g((b) => {
      b.add(new CylinderGeometry(0.07, 0.08, 0.1, 8), { matrix: mat4(0, 0.04, 0), color: COLORS.skinShade });
      b.add(new SphereGeometry(0.235, 16, 12), { matrix: mat4(0, 0.25, 0, 0, 0, 0, 1, 1.04, 1), color: skin });
      b.add(new SphereGeometry(0.242, 14, 10), { matrix: mat4(0, 0.27, 0.035), color: COLORS.hair });
      for (const sx of [-1, 1]) {
        b.add(new SphereGeometry(0.06, 8, 6), { matrix: mat4(sx * 0.235, 0.24, 0.01), color: skin });
        b.add(new SphereGeometry(0.04, 8, 6), { matrix: mat4(sx * 0.085, 0.27, -0.215, 0, 0, 0, 0.8, 1.2, 0.6), color: '#1b1b22' });
      }
      b.add(new SphereGeometry(0.035, 6, 5), { matrix: mat4(0, 0.21, -0.24), color: COLORS.skinShade });
      // backwards cap: dome + brim pointing back (+z)
      b.add(new SphereGeometry(0.255, 16, 8, 0, PI * 2, 0, PI / 2), { matrix: mat4(0, 0.3, 0.01), color: cap });
      b.add(boxGeo(0.26, 0.035, 0.2), { matrix: mat4(0, 0.33, 0.29, -0.15, 0, 0), color: cap });
      b.add(new SphereGeometry(0.03, 6, 4), { matrix: mat4(0, 0.555, 0.01), color: hoodie });
    }),
    upperArm: g((b) => {
      b.add(boxGeo(0.13, RIG.upperArm + 0.04, 0.13), { matrix: mat4(0, -RIG.upperArm / 2 + 0.02, 0), color: hoodie, shade: bakeShade });
    }),
    forearm: g((b) => {
      b.add(boxGeo(0.12, 0.22, 0.12), { matrix: mat4(0, -0.1, 0), color: hoodie, shade: bakeShade });
      b.add(boxGeo(0.125, 0.04, 0.125), { matrix: mat4(0, -0.21, 0), color: hoodDark });
      b.add(new SphereGeometry(0.075, 8, 6), { matrix: mat4(0, -0.28, 0), color: skin });
    }),
    thigh: g((b) => {
      b.add(boxGeo(0.16, RIG.thigh + 0.04, 0.17), { matrix: mat4(0, -RIG.thigh / 2, 0), color: jeans, shade: bakeShade });
    }),
    shin: g((b) => {
      b.add(boxGeo(0.145, 0.3, 0.155), { matrix: mat4(0, -0.15, 0), color: COLORS.jeansDark, shade: bakeShade });
      b.add(boxGeo(0.17, 0.11, 0.3), { matrix: mat4(0, -0.3, -0.06), color: COLORS.shoe });
      b.add(boxGeo(0.175, 0.035, 0.31), { matrix: mat4(0, -0.345, -0.06), color: '#d8d8d0' });
      b.add(boxGeo(0.178, 0.03, 0.12), { matrix: mat4(0, -0.27, -0.02), color: hoodie });
    }),
  };
}

// ---------------------------------------------------------------------------
// Environment
// ---------------------------------------------------------------------------
/** Extent of the periodic layer (local z: +ENV_BEHIND … -(ENV_AHEAD + PERIOD)). */
const P_START = ENV_BEHIND;
const P_END = -(ENV_AHEAD + PERIOD);

export function buildGravel(): BufferGeometry {
  const w = LAYOUT.wallInnerX * 2 + 0.2;
  const len = P_START - P_END;
  const g = groundQuad(w, len, [0, 0, w / GRAVEL_TILE, len / GRAVEL_TILE]);
  g.translate(0, 0, (P_START + P_END) / 2);
  return g;
}

/** Sleepers, poles, signals, cable troughs: repeats every PERIOD. */
export function buildPeriodic(): BufferGeometry {
  const b = new GeoBuilder();
  const sleeperGap = 0.8;
  const perN = Math.round(PERIOD / sleeperGap);
  for (let z = P_START; z > P_END; z -= sleeperGap) {
    const k = ((Math.round(z / sleeperGap) % perN) + perN) % perN;
    for (let l = -1; l <= 1; l++) {
      const c = 0.85 + ((k * 7 + (l + 1) * 3) % 5) * 0.04;
      b.add(new BoxGeometry(2.1, 0.12, 0.3), { matrix: mat4(l * LANE_WIDTH, 0.06, z), color: new Color(COLORS.sleeper).multiplyScalar(c) });
    }
  }
  for (let z = P_START; z > P_END; z -= PERIOD / 2) {
    for (const sx of [-1, 1]) {
      const x = sx * LAYOUT.poleX;
      b.add(new CylinderGeometry(0.11, 0.15, 9, 8), { matrix: mat4(x, 4.5, z), color: COLORS.pole });
      b.add(boxGeo(0.14, 0.14, 0.14), { matrix: mat4(x, 9, z), color: COLORS.poleDark });
      b.add(boxGeo(3.9, 0.12, 0.12), { matrix: mat4(x - sx * 1.95, 8.75, z), color: COLORS.poleDark });
      b.add(boxGeo(0.06, 0.6, 0.06), { matrix: mat4(x - sx * 2.6, 8.45, z, 0, 0, sx * 0.4), color: COLORS.poleDark });
      b.add(boxGeo(0.4, 0.15, 0.4), { matrix: mat4(x, 0.08, z), color: COLORS.poleDark });
    }
  }
  // signals every period on the left
  for (let z = P_START - PERIOD * 0.3; z > P_END; z -= PERIOD) {
    const x = -LAYOUT.poleX + 0.4;
    b.add(new CylinderGeometry(0.06, 0.06, 3.0, 6), { matrix: mat4(x, 1.5, z), color: '#3a3f48' });
    b.add(boxGeo(0.34, 0.8, 0.22), { matrix: mat4(x, 3.1, z), color: '#22262c' });
    b.add(new SphereGeometry(0.09, 8, 6), { matrix: mat4(x, 3.3, z + 0.12), color: '#ff3b2f' });
    b.add(new SphereGeometry(0.09, 8, 6), { matrix: mat4(x, 2.95, z + 0.12), color: '#3dff6a' });
  }
  // cable trough + track-side boxes
  for (let z = P_START; z > P_END; z -= 4) {
    for (const sx of [-1, 1]) {
      b.add(boxGeo(0.35, 0.18, 3.9), { matrix: mat4(sx * (LAYOUT.wallInnerX - 0.3), 0.09, z - 2), color: '#a9a49b' });
    }
    const zm = ((Math.round(z) % PERIOD) + PERIOD) % PERIOD;
    if (zm === 8 || zm === 28) {
      const sx = zm === 8 ? -1 : 1;
      b.add(boxGeo(0.5, 0.7, 0.4), { matrix: mat4(sx * 4.1, 0.35, z), color: '#8a96a3' });
    }
  }
  return b.build();
}

/** Never-moving: rails, overhead wires, outer ground. */
export function buildStatic(): BufferGeometry {
  const b = new GeoBuilder();
  const z0 = ENV_BEHIND + 20;
  const z1 = -(ENV_AHEAD + 60);
  const len = z0 - z1;
  const zc = (z0 + z1) / 2;
  for (let l = -1; l <= 1; l++) {
    for (const sx of [-1, 1]) {
      const x = l * LANE_WIDTH + sx * LAYOUT.railGauge;
      b.add(new BoxGeometry(0.09, 0.14, len), {
        matrix: mat4(x, 0.19, zc),
        color: COLORS.railSide,
        shade: (_x, _y, _z, _nx, ny, _nz, out) => {
          if (ny > 0.5) out.set(COLORS.railTop);
        },
      });
      b.add(new BoxGeometry(0.2, 0.03, len), { matrix: mat4(x, 0.125, zc), color: '#5a5048' });
    }
    b.add(new BoxGeometry(0.025, 0.025, len), { matrix: mat4(l * LANE_WIDTH, 8.2, zc), color: COLORS.wire });
    b.add(new BoxGeometry(0.018, 0.018, len), { matrix: mat4(l * LANE_WIDTH, 8.75, zc), color: COLORS.wire });
  }
  for (const sx of [-1, 1]) {
    const w = 120;
    b.add(groundQuad(w, len), { matrix: mat4(sx * (LAYOUT.wallInnerX + w / 2), -0.02, zc), color: COLORS.outerGround });
  }
  return b.build();
}

export interface SegmentGeos {
  wall: BufferGeometry;
  facade: BufferGeometry;
  props: BufferGeometry;
}

/** A 40 m decoration segment, local z 0 … -SEGMENT_LENGTH. */
export function buildSegment(variant: number): SegmentGeos {
  const r = new HashRng().seed(1234, variant);
  const L = SEGMENT_LENGTH;
  const wall = new GeoBuilder();
  const facade = new GeoBuilder();
  const props = new GeoBuilder();
  const bandH = 1 / WALL_BANDS;
  for (const sx of [-1, 1]) {
    const band = (variant * 2 + (sx > 0 ? 1 : 0)) % WALL_BANDS;
    const v1 = 1 - band * bandH;
    const v0 = v1 - bandH;
    const xIn = sx * LAYOUT.wallInnerX;
    wall.add(new PlaneGeometry(L, LAYOUT.wallHeight), {
      matrix: mat4(xIn, LAYOUT.wallHeight / 2, -L / 2, 0, -sx * (PI / 2)),
      uv: [0, v0, 1, v1],
    });
    // wall body + coping
    const xc = sx * (LAYOUT.wallInnerX + LAYOUT.wallThickness / 2 + 0.005);
    props.add(new BoxGeometry(LAYOUT.wallThickness, LAYOUT.wallHeight, L), { matrix: mat4(xc, LAYOUT.wallHeight / 2, -L / 2), color: '#b5ad9f' });
    props.add(new BoxGeometry(LAYOUT.wallThickness + 0.24, 0.2, L), { matrix: mat4(xc, LAYOUT.wallHeight + 0.1, -L / 2), color: '#e4ddd0' });
    // fence on top
    if (r.chance(0.6)) {
      for (let z = -1; z > -L; z -= 2.5) {
        props.add(boxGeo(0.06, 1.1, 0.06), { matrix: mat4(xc, LAYOUT.wallHeight + 0.75, z), color: '#6d7782' });
      }
      props.add(boxGeo(0.04, 0.04, L), { matrix: mat4(xc, LAYOUT.wallHeight + 1.25, -L / 2), color: '#6d7782' });
      props.add(boxGeo(0.04, 0.04, L), { matrix: mat4(xc, LAYOUT.wallHeight + 0.8, -L / 2), color: '#6d7782' });
    }
    // trees / bushes behind the wall
    let z = -r.range(2, 8);
    while (z > -L + 3) {
      const tx = sx * (LAYOUT.treeX + r.range(-0.5, 0.6));
      const h = r.range(3.6, 5.2);
      props.add(new CylinderGeometry(0.16, 0.22, h, 6), { matrix: mat4(tx, h / 2, z), color: '#7a5233' });
      const green = new Color(r.pick(['#4cc94a', '#3db85a', '#66d13d', '#2fa84f']));
      props.add(new IcosahedronGeometry(r.range(1.3, 1.8), 0), {
        matrix: mat4(tx, h + 0.6, z, r.next(), r.next(), 0, 1, r.range(0.85, 1.15), 1),
        color: green,
        flat: true,
        shade: (_x, _y, _z2, _nx, ny, _nz, out) => {
          if (ny < -0.2) out.multiplyScalar(0.75);
        },
      });
      z -= r.range(6, 12);
    }
    // buildings
    z = 0;
    while (z > -L) {
      const w = Math.min(r.range(8, 13), L + z);
      if (w < 3) break;
      const floors = r.int(3, 7);
      const h = floors * FACADE_TILE;
      const d = r.range(7, 11);
      const x = sx * (LAYOUT.buildingX + r.range(0, 1.5) + d / 2);
      const col = new Color(r.pick(PASTELS));
      const gap = r.range(0.3, 1.2);
      const bw = w - gap;
      const box = boxGeo(d, h, bw, {
        px: [0, 0, bw / FACADE_TILE, h / FACADE_TILE],
        nx: [0, 0, bw / FACADE_TILE, h / FACADE_TILE],
        pz: [0, 0, d / FACADE_TILE, h / FACADE_TILE],
        nz: [0, 0, d / FACADE_TILE, h / FACADE_TILE],
      }, FACADE_PLAIN);
      facade.add(box, {
        matrix: mat4(x, h / 2, z - w / 2),
        color: col,
        shade: (_x, _y, _z2, _nx, ny, _nz, out) => {
          if (ny > 0.5) out.multiplyScalar(0.8);
        },
      });
      // roof details
      props.add(boxGeo(d + 0.3, 0.35, bw + 0.3), { matrix: mat4(x, h + 0.17, z - w / 2), color: new Color(col).multiplyScalar(0.75) });
      if (r.chance(0.5)) {
        const tx = x + r.range(-1.5, 1.5);
        const tz = z - w / 2 + r.range(-2, 2);
        props.add(new CylinderGeometry(0.9, 0.9, 1.6, 10), { matrix: mat4(tx, h + 2.1, tz), color: '#a0703f' });
        props.add(new CylinderGeometry(0.0, 1.0, 0.6, 10), { matrix: mat4(tx, h + 3.2, tz), color: '#7a5233' });
        for (const lx of [-0.6, 0.6]) props.add(boxGeo(0.1, 1.3, 0.1), { matrix: mat4(tx + lx, h + 0.75, tz), color: '#5c6672' });
      } else if (r.chance(0.6)) {
        props.add(boxGeo(1.4, 0.9, 1.2), { matrix: mat4(x + r.range(-1, 1), h + 0.8, z - w / 2 + r.range(-2, 2)), color: '#c9d0d8' });
      }
      z -= w;
    }
    // back row (tall, muted)
    z = -r.range(0, 6);
    while (z > -L) {
      const w = r.range(10, 18);
      const h = r.int(7, 14) * FACADE_TILE;
      const d = 12;
      const col = new Color(r.pick(PASTELS)).lerp(new Color('#cfe6f5'), 0.35);
      facade.add(boxGeo(d, h, w, {
        px: [0, 0, w / FACADE_TILE, h / FACADE_TILE],
        nx: [0, 0, w / FACADE_TILE, h / FACADE_TILE],
        pz: [0, 0, d / FACADE_TILE, h / FACADE_TILE],
        nz: [0, 0, d / FACADE_TILE, h / FACADE_TILE],
      }, FACADE_PLAIN), { matrix: mat4(sx * (LAYOUT.backRowX + r.range(0, 6)), h / 2, z - w / 2), color: col });
      z -= w + r.range(2, 6);
    }
  }
  return { wall: wall.build(), facade: facade.build(), props: props.build() };
}

/** Sky dome with a vertical gradient baked into vertex colors. */
export function buildSky(): BufferGeometry {
  const g = new SphereGeometry(600, 24, 16);
  const pos = g.getAttribute('position');
  const col = new Float32Array(pos.count * 3);
  const top = new Color(COLORS.skyTop);
  const hor = new Color(COLORS.skyHorizon);
  const c = new Color();
  for (let i = 0; i < pos.count; i++) {
    const t = Math.max(0, Math.min(1, pos.getY(i) / 600));
    c.copy(hor).lerp(top, Math.pow(t, 0.55));
    col[i * 3] = c.r;
    col[i * 3 + 1] = c.g;
    col[i * 3 + 2] = c.b;
  }
  g.setAttribute('color', new Float32BufferAttribute(col, 3));
  return g;
}

export function buildClouds(): BufferGeometry {
  const b = new GeoBuilder();
  const r = new HashRng().seed(4242);
  for (let i = 0; i < 16; i++) {
    const a = -PI / 2 + r.range(-1.25, 1.25);
    const dist = r.range(260, 420);
    const cx = Math.cos(a) * dist;
    const cz = Math.sin(a) * dist;
    const cy = r.range(45, 110);
    const s = r.range(9, 18);
    const puffs = r.int(4, 7);
    for (let p = 0; p < puffs; p++) {
      const ps = s * r.range(0.6, 1.1);
      b.add(new IcosahedronGeometry(1, 1), {
        matrix: mat4(cx + (p - puffs / 2) * s * 0.75 + r.range(-2, 2), cy + r.range(-0.2, 0.4) * s, cz + r.range(-4, 4), 0, 0, 0, ps * 1.3, ps * 0.8, ps),
        color: '#ffffff',
        flat: true,
        shade: (_x, y, _z, _nx, ny, _nz, out) => {
          if (ny < -0.3 || y < cy - ps * 0.2) out.set('#d8ecfa');
        },
      });
    }
  }
  return b.build();
}
