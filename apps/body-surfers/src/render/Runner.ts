// Cartoon runner: jointed rig of shared geometries + procedural animation.
import { AdditiveBlending, Color, Group, Mesh, MeshToonMaterial, Sprite, SpriteMaterial } from 'three';
import type { BufferGeometry, Material, Object3D } from 'three';
import { clamp, damp, smoothstep } from '../shared/math';
import type { MimicPose, PlayerId, WorldSnapshot } from '../shared/types';
import type { Assets } from './Assets';
import { RIG } from './assets/models';

// Pose channel indices
const ROOT_Y = 0, ROOT_RX = 1, ROOT_RY = 2, ROOT_RZ = 3;
const TORSO_X = 4, TORSO_Y = 5, TORSO_Z = 6, HEAD_X = 7;
const SHL_X = 8, SHL_Z = 9, SHR_X = 10, SHR_Z = 11, EL_L = 12, EL_R = 13;
const HIPL_X = 14, HIPL_Z = 15, HIPR_X = 16, HIPR_Z = 17, KN_L = 18, KN_R = 19;
const ELZ_L = 20, ELZ_R = 21;
const CH = 22;

// Mimic (player pose copy) limits, radians. Upper: 0 = down, PI/2 = out, PI = up.
const MIMIC_UPPER_MIN = -0.25;
const MIMIC_UPPER_MAX = 3.05;
const MIMIC_LOWER_MIN = -0.4;
const MIMIC_LOWER_MAX = 2.3;
const MIMIC_LEAN_MAX = 0.45;
/** Smoothing rate for the incoming pose (1/s). */
const MIMIC_RATE = 14;
/** Smoothing rate for the blend weight (1/s); slow enough to never pop. */
const MIMIC_WEIGHT_RATE = 6;

const TAU = Math.PI * 2;
const GOLD = new Color('#ffd23f');

/** Power-up driven visual state, computed by the View once per frame. */
export interface RunnerFx {
  magnet: boolean;
  jetpack: boolean;
  star: boolean;
}

export class Runner {
  readonly root = new Group();
  private readonly spin = new Group();
  private readonly hips: Mesh;
  private readonly torso: Mesh;
  private readonly head: Mesh;
  private readonly shL: Mesh;
  private readonly shR: Mesh;
  private readonly elL: Mesh;
  private readonly elR: Mesh;
  private readonly hipL: Mesh;
  private readonly hipR: Mesh;
  private readonly knL: Mesh;
  private readonly knR: Mesh;
  readonly board: Mesh;
  readonly magnetRing: Mesh;
  readonly jetpack: Mesh;
  private readonly flames: Mesh[] = [];
  private readonly starGlow: Sprite;
  private readonly starMat: SpriteMaterial;
  private fly = 0;
  private readonly bodyMat: MeshToonMaterial;
  private readonly cur = new Float32Array(CH);
  private readonly tgt = new Float32Array(CH);
  private phase = 0;
  private flipT = -1;
  private clock = 0;
  private spinX = 0;
  // smoothed mimic pose + blend weight
  private mW = 0;
  private mLU = 0;
  private mLL = 0;
  private mRU = 0;
  private mRL = 0;
  private mLean = 0;

  constructor(assets: Assets, playerId: PlayerId) {
    const g = assets.runners[playerId];
    // own copy of the body material so the star aura can tint it per view
    const mat = assets.matVC.clone();
    this.bodyMat = mat;
    const mk = (geo: BufferGeometry, parent: Object3D, x: number, y: number, z = 0, m: Material = mat): Mesh => {
      const mesh = new Mesh(geo, m);
      mesh.position.set(x, y, z);
      mesh.castShadow = true;
      parent.add(mesh);
      return mesh;
    };
    this.root.add(this.spin);
    this.spin.position.y = 0.5;
    const body = new Group();
    body.position.y = -0.5;
    this.spin.add(body);
    this.hips = mk(g.pelvis, body, 0, RIG.hipY);
    this.torso = mk(g.torso, this.hips, 0, RIG.torsoY);
    this.head = mk(g.head, this.torso, 0, RIG.neckY);
    this.shL = mk(g.upperArm, this.torso, -RIG.shoulderX, RIG.shoulderY);
    this.shR = mk(g.upperArm, this.torso, RIG.shoulderX, RIG.shoulderY);
    this.elL = mk(g.forearm, this.shL, 0, -RIG.upperArm);
    this.elR = mk(g.forearm, this.shR, 0, -RIG.upperArm);
    this.hipL = mk(g.thigh, this.hips, -RIG.hipX, -0.05);
    this.hipR = mk(g.thigh, this.hips, RIG.hipX, -0.05);
    this.knL = mk(g.shin, this.hipL, 0, -RIG.thigh);
    this.knR = mk(g.shin, this.hipR, 0, -RIG.thigh);
    this.board = mk(assets.board, this.root, 0, 0, -0.05, assets.matProps);
    this.board.scale.set(1.25, 1, 1.25);
    this.board.visible = false;
    this.magnetRing = new Mesh(assets.ring, assets.matShield);
    this.magnetRing.position.y = 0.9;
    this.magnetRing.rotation.x = Math.PI / 2;
    this.magnetRing.visible = false;
    this.root.add(this.magnetRing);

    this.jetpack = mk(assets.jetpack, this.torso, 0, 0.24, 0.14, assets.matProps);
    this.jetpack.visible = false;
    for (const sx of [-1, 1]) {
      const f = new Mesh(assets.flame, assets.matFlame);
      f.position.set(sx * 0.12, -0.32, 0.16);
      f.renderOrder = 7;
      this.jetpack.add(f);
      this.flames.push(f);
    }
    this.starMat = new SpriteMaterial({ map: assets.glowSprite, color: GOLD, transparent: true, blending: AdditiveBlending, depthWrite: false, opacity: 0.9 });
    this.starGlow = new Sprite(this.starMat);
    this.starGlow.position.y = 0.95;
    this.starGlow.scale.set(2.6, 2.6, 1);
    this.starGlow.renderOrder = 8;
    this.starGlow.visible = false;
    this.root.add(this.starGlow);
  }

  /** World-space height of the jetpack nozzles above the feet. */
  get nozzleY(): number {
    return 0.62;
  }

  dispose(): void {
    this.starMat.dispose();
    this.bodyMat.dispose();
  }

  /** Super-jump front flip. */
  flip(): void {
    this.flipT = 0;
  }

  reset(): void {
    this.flipT = -1;
    this.cur.fill(0);
    this.spinX = 0;
    this.mW = 0;
  }

  update(s: WorldSnapshot, dt: number, fx: RunnerFx, mimic: MimicPose | null = null): void {
    const magnet = fx.magnet;
    const p = s.player;
    const t = this.tgt;
    t.fill(0);
    this.clock += dt;
    const at = p.animTime;
    const ready = s.status === 'ready';
    const surfing = p.hoverboard && !ready;
    let spin = 0;
    let rate = 18;
    const flying = fx.jetpack && p.alive && !ready && p.y > 0.6;
    this.fly = damp(this.fly, flying ? 1 : 0, 6, dt);

    if (ready) {
      const b = Math.sin(this.clock * 3.2);
      t[ROOT_Y] = 0.035 * Math.abs(b);
      t[TORSO_X] = 0.04 * b;
      t[HEAD_X] = 0.05 * Math.sin(this.clock * 1.6);
      t[SHL_Z] = -0.18 - 0.05 * b;
      t[SHR_Z] = 0.18 + 0.05 * b;
      t[SHL_X] = 0.1 * Math.sin(this.clock * 1.6);
      t[SHR_X] = -0.1 * Math.sin(this.clock * 1.6);
      t[EL_L] = 0.35;
      t[EL_R] = 0.35;
      t[KN_L] = -0.12 - 0.08 * Math.abs(b);
      t[KN_R] = -0.12 - 0.08 * Math.abs(b);
      t[HIPL_X] = 0.06 + 0.04 * Math.abs(b);
      t[HIPR_X] = 0.06 + 0.04 * Math.abs(b);
      t[HIPL_Z] = -0.04;
      t[HIPR_Z] = 0.04;
    } else if (flying) {
      // jetpack: leaning forward, legs trailing, arms out for balance
      const b = Math.sin(this.clock * 5);
      const sw = Math.sin(this.clock * 3.1);
      t[ROOT_Y] = 0.08 * b;
      t[ROOT_RX] = -0.32;
      t[TORSO_X] = -0.15;
      t[HEAD_X] = -0.25;
      t[HIPL_X] = -0.25 + 0.12 * sw;
      t[HIPR_X] = -0.05 - 0.12 * sw;
      t[KN_L] = -0.55;
      t[KN_R] = -0.35;
      t[SHL_X] = 0.35;
      t[SHR_X] = 0.35;
      t[SHL_Z] = -0.75 - 0.06 * b;
      t[SHR_Z] = 0.75 + 0.06 * b;
      t[EL_L] = 0.6;
      t[EL_R] = 0.6;
      t[ROOT_RZ] = -p.lean * 0.45;
      t[ROOT_RY] = -p.lean * 0.2;
      rate = 10;
    } else {
      switch (p.anim) {
        case 'run': {
          if (surfing && p.grounded) {
            const w = Math.sin(this.clock * 4);
            t[ROOT_Y] = 0.1 + 0.03 * w;
            t[ROOT_RY] = -0.35;
            t[TORSO_X] = -0.25;
            t[TORSO_Y] = 0.25;
            t[HIPL_X] = 0.55;
            t[HIPR_X] = -0.15;
            t[KN_L] = -0.75;
            t[KN_R] = -0.55;
            t[SHL_Z] = -1.1 - 0.1 * w;
            t[SHR_Z] = 1.0 + 0.1 * w;
            t[EL_L] = 0.4;
            t[EL_R] = 0.4;
            t[HEAD_X] = 0.2;
            break;
          }
          const freq = 1.25 + s.speed * 0.055;
          this.phase = (this.phase + dt * freq * TAU) % TAU;
          const sn = Math.sin(this.phase);
          const cs = Math.cos(this.phase);
          t[ROOT_Y] = 0.07 * Math.abs(cs);
          t[TORSO_X] = -0.2;
          t[TORSO_Y] = 0.16 * sn;
          t[HEAD_X] = 0.12;
          t[HIPL_X] = 0.85 * sn;
          t[HIPR_X] = -0.85 * sn;
          t[KN_L] = -(0.25 + 1.15 * Math.max(0, -Math.sin(this.phase + 0.7)));
          t[KN_R] = -(0.25 + 1.15 * Math.max(0, Math.sin(this.phase + 0.7)));
          t[SHL_X] = -0.85 * sn;
          t[SHR_X] = 0.85 * sn;
          t[SHL_Z] = -0.12;
          t[SHR_Z] = 0.12;
          t[EL_L] = 1.25 + 0.25 * sn;
          t[EL_R] = 1.25 - 0.25 * sn;
          rate = 26;
          break;
        }
        case 'jump': {
          const k = smoothstep(0, 0.18, at);
          t[TORSO_X] = -0.15 * k;
          t[HIPL_X] = 1.0 * k;
          t[HIPR_X] = 0.25 * k;
          t[KN_L] = -1.5 * k;
          t[KN_R] = -1.0 * k;
          t[SHL_X] = 2.5 * k;
          t[SHR_X] = 1.2 * k;
          t[SHL_Z] = -0.35;
          t[SHR_Z] = 0.6;
          t[EL_L] = 0.35;
          t[EL_R] = 0.6;
          t[HEAD_X] = 0.15;
          if (surfing) {
            t[HIPL_X] = 0.6;
            t[HIPR_X] = -0.1;
            t[KN_R] = -0.8;
          }
          break;
        }
        case 'fall': {
          const w = Math.sin(at * 14);
          t[HIPL_X] = 0.5;
          t[HIPR_X] = -0.15;
          t[KN_L] = -0.7;
          t[KN_R] = -0.4;
          t[SHL_X] = 2.0 + 0.45 * w;
          t[SHR_X] = 2.0 - 0.45 * w;
          t[SHL_Z] = -0.9;
          t[SHR_Z] = 0.9;
          t[EL_L] = 0.3;
          t[EL_R] = 0.3;
          t[TORSO_X] = 0.08;
          t[HEAD_X] = 0.2;
          break;
        }
        case 'roll': {
          const k = clamp(at / 0.65, 0, 1);
          spin = -TAU * smoothstep(0, 1, k);
          t[ROOT_Y] = -0.05;
          t[TORSO_X] = -0.9;
          t[HEAD_X] = -0.5;
          t[HIPL_X] = 1.9;
          t[HIPR_X] = 1.9;
          t[KN_L] = -2.3;
          t[KN_R] = -2.3;
          t[SHL_X] = 1.3;
          t[SHR_X] = 1.3;
          t[EL_L] = 1.9;
          t[EL_R] = 1.9;
          rate = 30;
          break;
        }
        case 'stumble': {
          const d = Math.exp(-at * 4);
          const w = Math.sin(at * 22) * d;
          const freq = 1.25 + s.speed * 0.055;
          this.phase = (this.phase + dt * freq * TAU) % TAU;
          const sn = Math.sin(this.phase);
          t[ROOT_RX] = 0.25 * d;
          t[TORSO_Z] = 0.4 * w;
          t[TORSO_X] = 0.2 * d;
          t[HEAD_X] = 0.3 * d;
          t[SHL_X] = 1.6 + w;
          t[SHR_X] = 1.6 - w;
          t[SHL_Z] = -1.2;
          t[SHR_Z] = 1.2;
          t[HIPL_X] = 0.7 * sn;
          t[HIPR_X] = -0.7 * sn;
          t[KN_L] = -0.6;
          t[KN_R] = -0.6;
          break;
        }
        case 'dead': {
          const k = smoothstep(0, 0.45, at);
          const bounce = at > 0.45 ? Math.exp(-(at - 0.45) * 8) * Math.abs(Math.sin((at - 0.45) * 18)) * 0.12 : 0;
          t[ROOT_RX] = 1.45 * k;
          t[ROOT_Y] = 0.12 * k + bounce;
          t[SHL_X] = 2.8 * k;
          t[SHR_X] = 2.6 * k;
          t[SHL_Z] = -0.7 * k;
          t[SHR_Z] = 0.8 * k;
          t[HIPL_X] = 0.35 * k;
          t[HIPR_X] = 0.1 * k;
          t[KN_L] = -0.4 * k;
          t[HIPL_Z] = -0.2 * k;
          t[HIPR_Z] = 0.25 * k;
          t[HEAD_X] = 0.3 * k;
          rate = 30;
          break;
        }
      }
      // lean into lane changes
      if (p.alive) {
        t[ROOT_RZ] += -p.lean * 0.32;
        t[ROOT_RY] += -p.lean * 0.22;
      }
      // push against a train alongside
      if (p.blockedDir !== 0 && p.alive && !p.rolling) {
        const dir = p.blockedDir;
        const j = 0.05 * Math.sin(this.clock * 40);
        t[TORSO_Z] += -dir * 0.3 + j;
        t[ROOT_RZ] += -dir * 0.12;
        if (dir > 0) {
          t[SHR_Z] = 1.45;
          t[SHR_X] = 0.3;
          t[EL_R] = 0.2;
        } else {
          t[SHL_Z] = -1.45;
          t[SHL_X] = 0.3;
          t[EL_L] = 0.2;
        }
      }
    }

    // super-jump flip overrides the spin channel
    if (this.flipT >= 0) {
      this.flipT += dt;
      const k = clamp(this.flipT / 0.75, 0, 1);
      spin = -TAU * smoothstep(0, 1, k);
      if (k >= 1 || p.grounded) this.flipT = -1;
    }

    this.applyMimic(p.anim, p.alive, mimic, dt);

    const c = this.cur;
    for (let i = 0; i < CH; i++) c[i] = damp(c[i], t[i], rate, dt);
    // the spin channel is applied directly (it wraps a full turn)
    this.spinX = spin;

    this.root.position.set(p.x, p.y + c[ROOT_Y], 0);
    this.root.rotation.set(c[ROOT_RX], c[ROOT_RY], c[ROOT_RZ]);
    this.spin.rotation.x = this.spinX;
    this.torso.rotation.set(c[TORSO_X], c[TORSO_Y], c[TORSO_Z]);
    this.head.rotation.x = c[HEAD_X];
    this.shL.rotation.set(c[SHL_X], 0, c[SHL_Z]);
    this.shR.rotation.set(c[SHR_X], 0, c[SHR_Z]);
    this.elL.rotation.set(c[EL_L], 0, c[ELZ_L]);
    this.elR.rotation.set(c[EL_R], 0, c[ELZ_R]);
    this.hipL.rotation.set(c[HIPL_X], 0, c[HIPL_Z]);
    this.hipR.rotation.set(c[HIPR_X], 0, c[HIPR_Z]);
    this.knL.rotation.x = c[KN_L];
    this.knR.rotation.x = c[KN_R];

    // hoverboard under the feet (counter the body offset)
    this.board.visible = surfing;
    if (surfing) this.board.position.y = -c[ROOT_Y] + 0.02 + (p.grounded ? 0.06 : 0);

    // magnet shimmer
    this.magnetRing.visible = magnet && p.alive;
    if (magnet) {
      const pulse = 1 + 0.12 * Math.sin(this.clock * 9);
      this.magnetRing.scale.set(pulse, pulse, pulse);
      this.magnetRing.rotation.z = this.clock * 3;
    }

    // jetpack + flame flicker
    this.jetpack.visible = fx.jetpack && p.alive;
    if (this.jetpack.visible) {
      const thrust = 0.35 + 0.65 * Math.max(this.fly, p.vy > 0 ? 1 : 0);
      for (let i = 0; i < this.flames.length; i++) {
        const fl = 0.85 + 0.25 * Math.sin(this.clock * 47 + i * 2.1) + 0.12 * Math.sin(this.clock * 83 + i);
        const w = 0.9 + 0.15 * Math.sin(this.clock * 61 + i * 1.3);
        this.flames[i].scale.set(w, thrust * fl * 0.9, w);
      }
    }

    // star: rainbow-gold aura
    const star = fx.star && p.alive;
    this.starGlow.visible = star;
    if (star) {
      const hue = (this.clock * 0.6) % 1;
      this.starMat.color.setHSL(hue, 1, 0.6).lerp(GOLD, 0.45);
      const pulse = 2.5 + 0.35 * Math.sin(this.clock * 10);
      this.starGlow.scale.set(pulse, pulse * 1.15, 1);
      const blink = p.invulnerable > 0 && Math.floor(this.clock * 12) % 2 === 0;
      this.starMat.opacity = blink ? 0.35 : 0.95;
      this.bodyMat.emissive.copy(this.starMat.color).multiplyScalar(blink ? 0.15 : 0.45);
    } else if (this.bodyMat.emissive.r + this.bodyMat.emissive.g + this.bodyMat.emissive.b > 0) {
      this.bodyMat.emissive.setRGB(0, 0, 0);
    }

    // invulnerability flash (the star aura blinks instead of the body)
    this.spin.visible = star || !(p.invulnerable > 0 && Math.floor(this.clock * 14) % 2 === 0);
  }

  /**
   * Blend the player's arm/torso pose over the procedural targets in `tgt`.
   * Seen from behind, screen-left is world -x, which is the shL arm. Rotation about +z
   * swings a hanging arm toward -x for negative angles, so the left side is negated.
   */
  private applyMimic(anim: string, alive: boolean, m: MimicPose | null, dt: number): void {
    let want = 0;
    if (m && m.weight > 0) {
      want = clamp(m.weight, 0, 1);
      // keep the big readable animations readable
      if (!alive || anim === 'dead') want = 0;
      else if (anim === 'roll') want *= 0.15;
      else if (anim === 'stumble') want *= 0.3;
      else if (anim === 'jump' || anim === 'fall') want *= 0.85;
      const r = this.mW < 0.01 ? 1 : 1 - Math.exp(-MIMIC_RATE * dt);
      // snap the pose on first engage (weight is still ~0 so nothing pops)
      this.mLU += (clamp(m.leftUpper, MIMIC_UPPER_MIN, MIMIC_UPPER_MAX) - this.mLU) * r;
      this.mRU += (clamp(m.rightUpper, MIMIC_UPPER_MIN, MIMIC_UPPER_MAX) - this.mRU) * r;
      this.mLL += (clamp(m.leftLower, MIMIC_LOWER_MIN, MIMIC_LOWER_MAX) - this.mLL) * r;
      this.mRL += (clamp(m.rightLower, MIMIC_LOWER_MIN, MIMIC_LOWER_MAX) - this.mRL) * r;
      this.mLean += (clamp(m.lean, -MIMIC_LEAN_MAX, MIMIC_LEAN_MAX) - this.mLean) * r;
    }
    this.mW = damp(this.mW, want, MIMIC_WEIGHT_RATE, dt);
    const w = this.mW;
    if (w < 0.001) return;
    const t = this.tgt;
    const k = 1 - w;
    // arms in the screen plane: no forward swing, no forward elbow bend
    t[SHL_X] *= k;
    t[SHR_X] *= k;
    t[EL_L] *= k;
    t[EL_R] *= k;
    t[SHL_Z] = t[SHL_Z] * k - this.mLU * w;
    t[SHR_Z] = t[SHR_Z] * k + this.mRU * w;
    t[ELZ_L] = t[ELZ_L] * k - this.mLL * w;
    t[ELZ_R] = t[ELZ_R] * k + this.mRL * w;
    // + lean = top toward screen-right (+x) = negative rotation about z
    t[TORSO_Z] = t[TORSO_Z] * k - this.mLean * w;
    t[TORSO_Y] *= k;
  }
}
