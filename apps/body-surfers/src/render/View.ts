// One player's scene: environment, pooled obstacles/collectibles, runner, camera rig, effects.
import {
  Color,
  DirectionalLight,
  Fog,
  Group,
  HemisphereLight,
  InstancedMesh,
  Mesh,
  Object3D,
  PerspectiveCamera,
  Scene,
} from 'three';
import { BARRIER_HIGH_DEPTH, BARRIER_LOW_DEPTH, TRAIN_CAR_LENGTH, VIEW_DISTANCE, laneX } from '../shared/constants';
import { clamp, damp, lerp } from '../shared/math';
import type {
  CollectibleKind,
  GameEvent,
  ObstacleKind,
  ObstacleState,
  PlayerId,
  PowerUpKind,
  WorldSnapshot,
} from '../shared/types';
import { POWERUP_KINDS, SEGMENT_VARIANTS } from './Assets';
import type { Assets } from './Assets';
import { reducedMotion } from '../shared/motion';
import { CAMERA, COLORS, ENV_AHEAD, ENV_BEHIND, PERIOD, POWERUP_COLORS, QUALITY, SEGMENT_LENGTH } from './config';
import type { QualityProfile } from './config';
import { Particles, ScreenTint, SpeedLines } from './Particles';
import { Runner } from './Runner';
import type { RunnerFx } from './Runner';
import { HashRng, hash2 } from './util/rand';
import type { MimicPose, Quality } from '../shared/types';

// Coin rain (~140) + jetpack sky trail + course coins can all be live at once.
const MAX_COINS = 480;
/** Coins this far behind the runner (m) are behind the camera: don't spend instances on them. */
const COIN_BEHIND_CULL = 3;
const C_DUST = new Color('#d8c7a8');
const C_DUST_LIGHT = new Color('#e8dcc4');
const C_SPARK = new Color('#ffe680');
const C_COIN = new Color('#ffe14d');
const C_WHITE = new Color('#ffffff');
const C_STAR = new Color('#ffd23f');
const C_PINK = new Color('#ff7ad9');
const C_CYAN = new Color('#3df2ff');
const C_POWER: Record<PowerUpKind, Color> = {
  magnet: new Color(POWERUP_COLORS.magnet),
  multiplier: new Color(POWERUP_COLORS.multiplier),
  sneakers: new Color(POWERUP_COLORS.sneakers),
  hoverboard: new Color(POWERUP_COLORS.hoverboard),
  jetpack: new Color(POWERUP_COLORS.jetpack),
  slowmo: new Color(POWERUP_COLORS.slowmo),
  star: new Color(POWERUP_COLORS.star),
  coinRain: new Color(POWERUP_COLORS.coinRain),
};
const C_SMOKE = new Color('#7d858f');
const C_FIRE = new Color('#ff9a2e');
const C_DEBRIS = new Color('#a8aeb6');
const C_DEBRIS_RED = new Color('#e8323e');
const C_SLOWMO = new Color('#3d7bff');
const C_SLOW_LINES = new Color('#9fd0ff');
const C_GOLD_TINT = new Color('#ffb300');
const C_FOG = new Color(COLORS.fog);
const C_FOG_SLOW = new Color('#a9c6ee');
const C_HEMI = new Color(COLORS.hemiSky);
const C_HEMI_SLOW = new Color('#a9c4ff');
/** Per-kind pickup scale so every power-up reads at the same size from far away. */
const PICKUP_SCALE: Partial<Record<PowerUpKind, number>> = { jetpack: 1.7, slowmo: 1.15, coinRain: 1.35 };
/** Speed (m/s) beyond which speed effects keep intensifying, and the span to max out. */
const OVERDRIVE_FROM = 27;
const OVERDRIVE_SPAN = 18;
const DEG = Math.PI / 180;

interface ObSlot {
  obj: Object3D;
  body: Mesh;
  glow: Mesh | null;
  kind: ObstacleKind;
  stamp: number;
}

interface PuSlot {
  obj: Group;
  item: Mesh;
  halo: Mesh;
  kind: PowerUpKind;
  stamp: number;
}

interface SegSlot {
  group: Group;
  wall: Mesh;
  facade: Mesh;
  props: Mesh;
  k: number;
}

export class View {
  readonly scene = new Scene();
  readonly camera = new PerspectiveCamera(CAMERA.fovMin, 1, CAMERA.near, CAMERA.far);
  private readonly hemi: HemisphereLight;
  private readonly sun: DirectionalLight;
  private readonly sky: Mesh;
  private readonly clouds: Mesh;
  private readonly periodic = new Group();
  private readonly segs: SegSlot[] = [];
  private readonly runner: Runner;
  private readonly coins: InstancedMesh;
  private readonly particles: Particles;
  private readonly speedLines: SpeedLines;
  private readonly dummy = new Object3D();
  private readonly tint = new ScreenTint();
  private readonly fx: RunnerFx = { magnet: false, jetpack: false, star: false };
  private slow = 0;
  private starAmt = 0;
  private fly = 0;
  private smokeAcc = 0;
  private sparkleAcc = 0;
  private starAcc = 0;
  private readonly starColor = new Color();

  private readonly obMap = new Map<number, ObSlot>();
  private readonly obPools: Record<ObstacleKind, ObSlot[]> = { train: [], barrier_low: [], barrier_high: [], ramp: [] };
  private readonly puMap = new Map<number, PuSlot>();
  private readonly puPools: Record<PowerUpKind, PuSlot[]> = { magnet: [], multiplier: [], sneakers: [], hoverboard: [], jetpack: [], slowmo: [], star: [], coinRain: [] };
  private stamp = 0;

  private readonly rng = new HashRng().seed(99);
  private readonly rand = (): number => this.rng.next();
  private readonly sweepOb = (slot: ObSlot, id: number): void => {
    if (slot.stamp !== this.stamp) {
      this.releaseOb(slot);
      this.obMap.delete(id);
    }
  };
  private readonly sweepPu = (slot: PuSlot, id: number): void => {
    if (slot.stamp !== this.stamp) {
      slot.obj.visible = false;
      this.puPools[slot.kind].push(slot);
      this.puMap.delete(id);
    }
  };
  private readonly releaseObCb = (slot: ObSlot): void => this.releaseOb(slot);
  private readonly releasePuCb = (slot: PuSlot): void => {
    slot.obj.visible = false;
    this.puPools[slot.kind].push(slot);
  };

  // camera rig state
  private camX = 0;
  private camY: number = CAMERA.posY;
  private lookY: number = CAMERA.lookY;
  private fov: number = CAMERA.fovMin;
  private fovKick = 0;
  private shake = 0;
  private flash = 0;
  private time = 0;
  private lastSeed = -1;
  private lastDistance = 0;
  private first = true;
  private aspect = 1;
  private quality: QualityProfile = QUALITY.high;

  constructor(private readonly assets: Assets, readonly playerId: PlayerId) {
    const sc = this.scene;
    sc.fog = new Fog(COLORS.fog, 55, VIEW_DISTANCE + 5);
    sc.background = new Color(COLORS.skyHorizon);
    this.hemi = new HemisphereLight(COLORS.hemiSky, COLORS.hemiGround, 1.9);
    sc.add(this.hemi);
    this.sun = new DirectionalLight(COLORS.sun, 2.4);
    this.sun.shadow.camera.left = -14;
    this.sun.shadow.camera.right = 14;
    this.sun.shadow.camera.top = 24;
    this.sun.shadow.camera.bottom = -14;
    this.sun.shadow.camera.near = 1;
    this.sun.shadow.camera.far = 90;
    this.sun.shadow.bias = -0.0008;
    this.sun.shadow.normalBias = 0.03;
    sc.add(this.sun, this.sun.target);

    this.sky = new Mesh(assets.sky, assets.matSky);
    this.sky.renderOrder = -10;
    this.sky.frustumCulled = false;
    this.clouds = new Mesh(assets.clouds, assets.matCloud);
    this.clouds.frustumCulled = false;
    sc.add(this.sky, this.clouds);

    const gravel = new Mesh(assets.gravel, assets.matGround);
    gravel.receiveShadow = true;
    const per = new Mesh(assets.periodic, assets.matVC);
    per.receiveShadow = true;
    this.periodic.add(gravel, per);
    sc.add(this.periodic);
    const stat = new Mesh(assets.staticEnv, assets.matVC);
    stat.receiveShadow = true;
    sc.add(stat);

    const nSeg = Math.ceil((ENV_AHEAD + ENV_BEHIND) / SEGMENT_LENGTH) + 1;
    for (let i = 0; i < nSeg; i++) {
      const s0 = assets.segments[0];
      const group = new Group();
      const wall = new Mesh(s0.wall, assets.matWall);
      const facade = new Mesh(s0.facade, assets.matFacade);
      const props = new Mesh(s0.props, assets.matVC);
      wall.receiveShadow = true;
      group.add(wall, facade, props);
      sc.add(group);
      this.segs.push({ group, wall, facade, props, k: Number.NaN });
    }

    this.runner = new Runner(assets, playerId);
    sc.add(this.runner.root);

    this.coins = new InstancedMesh(assets.coin, assets.matCoin, MAX_COINS);
    this.coins.frustumCulled = false;
    this.coins.castShadow = false;
    this.coins.count = 0;
    sc.add(this.coins);

    this.particles = new Particles(900);
    sc.add(this.particles.points);
    this.speedLines = new SpeedLines(48, this.rand);
    sc.add(this.speedLines.lines);
    sc.add(this.tint.mesh);

    // pre-warm pools
    for (let i = 0; i < 10; i++) this.releaseOb(this.createOb('train'));
    for (let i = 0; i < 8; i++) this.releaseOb(this.createOb('barrier_low'));
    for (let i = 0; i < 8; i++) this.releaseOb(this.createOb('barrier_high'));
    for (let i = 0; i < 4; i++) this.releaseOb(this.createOb('ramp'));
    for (const k of POWERUP_KINDS) {
      const s = this.createPu(k);
      s.obj.visible = false;
      this.puPools[k].push(s);
    }
  }

  setQuality(q: Quality): void {
    this.quality = QUALITY[q];
    const p = this.quality;
    this.sun.castShadow = p.shadows;
    if (p.shadows) {
      this.sun.shadow.mapSize.set(p.shadowMapSize, p.shadowMapSize);
      if (this.sun.shadow.map) {
        this.sun.shadow.map.dispose();
        this.sun.shadow.map = null;
      }
    }
    this.particles.scale = p.particleScale;
  }

  // ---------------------------------------------------------------------------
  update(s: WorldSnapshot, events: readonly GameEvent[], dt: number, aspect: number, heightPx: number, mimic: MimicPose | null = null): void {
    this.time += dt;
    this.aspect = aspect;
    const d = s.distance;
    if (s.seed !== this.lastSeed || d < this.lastDistance - 0.5) this.onReset();
    this.lastSeed = s.seed;
    this.lastDistance = d;

    const fx = this.fx;
    fx.magnet = false;
    fx.jetpack = false;
    fx.star = false;
    let slowmo = false;
    let coinRain = false;
    for (let i = 0; i < s.powerUps.length; i++) {
      const k = s.powerUps[i].kind;
      if (k === 'magnet') fx.magnet = true;
      else if (k === 'jetpack') fx.jetpack = true;
      else if (k === 'star') fx.star = true;
      else if (k === 'slowmo') slowmo = true;
      else if (k === 'coinRain') coinRain = true;
    }
    const running = s.status === 'running';
    this.slow = damp(this.slow, slowmo && running ? 1 : 0, 4, dt);
    this.starAmt = damp(this.starAmt, fx.star && running ? 1 : 0, 6, dt);
    this.fly = damp(this.fly, fx.jetpack && running && s.player.y > 1 ? 1 : 0, 2.5, dt);

    for (let i = 0; i < events.length; i++) this.onEvent(events[i], s);

    this.runner.update(s, dt, fx, mimic);
    if (running) this.ambientFx(s, dt, fx.jetpack, coinRain);
    this.updateEnv(d);
    this.syncObstacles(s);
    this.syncCollectibles(s);
    this.updateCamera(s, dt);

    const worldSpeed = s.status === 'running' ? s.speed : 0;
    this.particles.update(dt, worldSpeed);
    this.particles.setScale(heightPx / (2 * Math.tan((this.camera.fov * DEG) / 2)));
    const over = this.overdrive(s);
    const sl = this.slow;
    const base = clamp((s.speedLevel - 0.35) / 0.4, 0, 1) + over * 0.8 + this.fly * 0.4;
    const lines = this.quality.speedLines && running ? Math.max(base, sl * 0.9) : 0;
    this.speedLines.setStyle(sl > 0.5 ? C_SLOW_LINES : C_WHITE, 1 + sl * 1.6 + over * 0.5, 1 - sl * 0.45);
    this.speedLines.update(dt, worldSpeed, lines, this.camera.position.x, this.camera.position.y, 0, this.rand);

    // mood: slow-mo cools + mutes the world, star warms the screen edge
    const pulse = 0.5 + 0.5 * Math.sin(this.time * 7);
    if (sl > 0.01) {
      this.tint.color.copy(C_SLOWMO);
      this.tint.set(0.55 * sl, 0.05 * sl, aspect);
    } else if (this.starAmt > 0.01) {
      this.tint.color.copy(C_GOLD_TINT);
      this.tint.set(this.starAmt * (0.22 + 0.16 * pulse), 0, aspect);
    } else {
      this.tint.set(0, 0, aspect);
    }
    (this.scene.fog as Fog).color.copy(C_FOG).lerp(C_FOG_SLOW, sl * 0.8);
    this.hemi.color.copy(C_HEMI).lerp(C_HEMI_SLOW, sl * 0.7);

    // impact flash
    this.flash = Math.max(0, this.flash - dt * 4);
    const flash = reducedMotion() ? this.flash * 0.25 : this.flash; // no bright strobe on crash
    this.hemi.intensity = 1.9 + flash * 2.5 - sl * 0.3 + this.starAmt * 0.15;
  }

  /** 0..1 extra intensity once the world speed passes the old max. */
  private overdrive(s: WorldSnapshot): number {
    return s.status === 'running' ? clamp((s.speed - OVERDRIVE_FROM) / OVERDRIVE_SPAN, 0, 1) : 0;
  }

  /** Continuous emitters: jetpack smoke/fire, coin-rain sparkle. */
  private ambientFx(s: WorldSnapshot, dt: number, jetpack: boolean, coinRain: boolean): void {
    const p = s.player;
    const r = this.rand;
    if (this.fx.star && p.alive) {
      this.starAcc += dt * 30;
      while (this.starAcc >= 1) {
        this.starAcc -= 1;
        const c = this.starColor.setHSL(r(), 1, 0.65);
        this.particles.burst(1, p.x, p.y + 0.9, 0.2, 1.1, 1.5, 0.6, c, 0.22, 0.6, 0.5, 1, r);
      }
    } else this.starAcc = 0;
    if (jetpack && p.alive) {
      this.smokeAcc += dt * 38;
      const ny = p.y + this.runner.nozzleY;
      while (this.smokeAcc >= 1) {
        this.smokeAcc -= 1;
        const sx = r() < 0.5 ? -0.12 : 0.12;
        this.particles.burst(1, p.x + sx, ny - 0.15, 0.45, 0.08, 0.5, -0.4, C_SMOKE, 0.55, 0.9, -0.6, 1, r);
        this.particles.burst(1, p.x + sx, ny - 0.05, 0.4, 0.04, 0.6, -2.5, C_FIRE, 0.2, 0.22, 0, 1, r);
      }
    } else this.smokeAcc = 0;
    if (coinRain) {
      this.sparkleAcc += dt * 130;
      while (this.sparkleAcc >= 1) {
        this.sparkleAcc -= 1;
        const x = (r() - 0.5) * 11;
        const y = 0.6 + r() * 6.5;
        const z = -4 - r() * 34;
        this.particles.burst(1, x, y, z, 0, 0.25, -0.3, r() < 0.3 ? C_WHITE : C_COIN, 0.18 + r() * 0.16, 1.1, 0.6, 1, r);
      }
    } else this.sparkleAcc = 0;
  }

  private onReset(): void {
    this.obMap.forEach(this.releaseObCb);
    this.obMap.clear();
    this.puMap.forEach(this.releasePuCb);
    this.puMap.clear();
    this.particles.clear();
    this.runner.reset();
    this.first = true;
    this.shake = 0;
    this.fovKick = 0;
    this.flash = 0;
    this.slow = 0;
    this.starAmt = 0;
    this.fly = 0;
  }

  private onEvent(e: GameEvent, s: WorldSnapshot): void {
    const p = s.player;
    const scroll = 1;
    switch (e.type) {
      case 'jump':
        if (e.super) {
          this.runner.flip();
          this.fovKick += 5;
          this.particles.burst(16, p.x, p.y + 0.1, 0, 0.6, 3, 1.5, C_POWER.sneakers, 0.35, 0.6, 2, scroll, this.rand);
        }
        this.particles.burst(8, p.x, p.y + 0.05, 0.1, 0.5, 1.5, 0.8, C_DUST, 0.4, 0.45, 2, scroll, this.rand);
        break;
      case 'land':
        this.particles.burst(12, p.x, p.y + 0.05, 0.1, 0.6, 2.2, 0.6, C_DUST, 0.45, 0.5, 2, scroll, this.rand);
        this.shake = Math.max(this.shake, 0.05);
        break;
      case 'roll':
        this.particles.burst(10, p.x, 0.1, 0.2, 0.6, 1.8, 0.5, C_DUST, 0.45, 0.5, 2, scroll, this.rand);
        break;
      case 'laneChange':
        this.particles.burst(4, p.x, p.y + 0.1, 0.1, 0.3, 1.2, 0.4, C_DUST_LIGHT, 0.3, 0.35, 1, scroll, this.rand);
        break;
      case 'blocked':
        this.shake = Math.max(this.shake, 0.3);
        this.particles.burst(14, p.x + e.dir * 0.7, p.y + 1.1, 0, 0.3, 4, 1, C_SPARK, 0.22, 0.4, 6, 0, this.rand);
        break;
      case 'coin':
        this.particles.burst(7, p.x + e.x, p.y + e.y, 0, 0.3, 3.2, 0.6, C_COIN, 0.22, 0.4, 2, 0, this.rand);
        break;
      case 'powerUp':
        this.fovKick += 4;
        this.particles.burst(36, p.x, p.y + 1, 0, 0.8, 5, 1.5, C_POWER[e.kind], 0.35, 0.8, 3, 0, this.rand);
        break;
      case 'powerUpEnd':
        this.particles.burst(12, p.x, p.y + 1, 0, 0.6, 2, 0.5, C_POWER[e.kind], 0.25, 0.5, 1, 0, this.rand);
        break;
      case 'hoverboardBreak':
        this.shake = Math.max(this.shake, 0.55);
        this.flash = 0.6;
        this.particles.burst(46, p.x, p.y + 0.4, 0, 0.6, 7, 2.5, C_POWER.hoverboard, 0.35, 0.9, 9, 0, this.rand);
        break;
      case 'crash':
        this.shake = Math.max(this.shake, 0.9);
        this.flash = 1;
        this.particles.burst(30, p.x, p.y + 1.5, -0.4, 0.5, 6, 2, C_WHITE, 0.4, 0.6, 4, 0, this.rand);
        this.particles.burst(24, p.x, p.y + 1.7, -0.3, 0.6, 3, 1.5, C_STAR, 0.35, 1.3, 1.5, 0, this.rand);
        break;
      case 'speedUp':
        this.fovKick += 3;
        break;
      case 'smash': {
        const x = laneX(e.lane);
        const y = e.kind === 'train' ? 1.8 : e.kind === 'barrier_high' ? 1.4 : 0.7;
        const z = -1.2;
        this.shake = Math.max(this.shake, e.kind === 'train' ? 0.5 : 0.35);
        this.fovKick += 2.5;
        this.flash = Math.max(this.flash, 0.35);
        this.particles.burst(26, x, y, z, 1.2, 8, 3, C_DEBRIS, 0.32, 0.9, 12, 0.6, this.rand);
        this.particles.burst(10, x, y, z, 1, 7, 3, C_DEBRIS_RED, 0.28, 0.8, 12, 0.6, this.rand);
        this.particles.burst(22, x, y + 0.3, z, 0.8, 6, 2.5, C_STAR, 0.36, 1.0, 3, 0.3, this.rand);
        this.particles.burst(10, x, y + 0.3, z, 0.6, 9, 1, C_WHITE, 0.25, 0.35, 0, 0.3, this.rand);
        break;
      }
      case 'milestone':
        this.particles.burst(30, p.x, p.y + 2.6, -3, 2, 4, 2, C_PINK, 0.3, 1.2, 4, 0, this.rand);
        this.particles.burst(30, p.x, p.y + 2.6, -3, 2, 4, 2, C_CYAN, 0.3, 1.2, 4, 0, this.rand);
        break;
      default:
        break;
    }
  }

  // ---------------------------------------------------------------------------
  private updateEnv(d: number): void {
    this.periodic.position.z = ((d % PERIOD) + PERIOD) % PERIOD;
    const n = this.segs.length;
    const k0 = Math.floor((d - ENV_BEHIND) / SEGMENT_LENGTH);
    for (let j = 0; j < n; j++) {
      const k = k0 + j;
      const slot = this.segs[((k % n) + n) % n];
      if (slot.k !== k) {
        slot.k = k;
        const g = this.assets.segments[hash2(k, 77) % SEGMENT_VARIANTS];
        slot.wall.geometry = g.wall;
        slot.facade.geometry = g.facade;
        slot.props.geometry = g.props;
      }
      slot.group.position.z = -(k * SEGMENT_LENGTH - d);
    }
  }

  private createOb(kind: ObstacleKind): ObSlot {
    const a = this.assets;
    let body: Mesh;
    let glow: Mesh | null = null;
    let obj: Object3D;
    if (kind === 'train') {
      body = new Mesh(a.trains[1], a.matLivery[0]);
      glow = new Mesh(a.headlight, a.matHeadlight);
      glow.renderOrder = 5;
      const g = new Group();
      g.add(body, glow);
      obj = g;
      body.receiveShadow = true;
    } else {
      body = new Mesh(kind === 'ramp' ? a.ramp : kind === 'barrier_low' ? a.barrierLow : a.barrierHigh, a.matProps);
      obj = body;
      body.receiveShadow = kind === 'ramp';
    }
    body.castShadow = true;
    this.scene.add(obj);
    return { obj, body, glow, kind, stamp: 0 };
  }

  private releaseOb(slot: ObSlot): void {
    slot.obj.visible = false;
    this.obPools[slot.kind].push(slot);
  }

  private syncObstacles(s: WorldSnapshot): void {
    const st = ++this.stamp;
    const d = s.distance;
    const obs = s.obstacles;
    for (let i = 0; i < obs.length; i++) {
      const o = obs[i];
      let slot = this.obMap.get(o.id);
      if (slot && slot.kind !== o.kind) {
        this.releaseOb(slot);
        slot = undefined;
      }
      if (!slot) {
        slot = this.obPools[o.kind].pop() ?? this.createOb(o.kind);
        this.obMap.set(o.id, slot);
      }
      slot.stamp = st;
      this.placeOb(slot, o, d);
    }
    this.obMap.forEach(this.sweepOb);
  }

  private placeOb(slot: ObSlot, o: ObstacleState, d: number): void {
    const z = -(o.s - d);
    const x = laneX(o.lane);
    slot.obj.visible = true;
    switch (o.kind) {
      case 'train': {
        const cars = clamp(Math.round(o.length / TRAIN_CAR_LENGTH), 1, 3);
        slot.body.geometry = this.assets.trains[cars];
        slot.body.material = this.assets.matLivery[((o.variant % 5) + 5) % 5];
        if (slot.glow) slot.glow.visible = o.moving;
        slot.obj.position.set(x, 0, z);
        break;
      }
      case 'barrier_low':
        slot.obj.position.set(x, 0, z - (o.length || BARRIER_LOW_DEPTH) / 2);
        break;
      case 'barrier_high':
        slot.obj.position.set(x, 0, z - (o.length || BARRIER_HIGH_DEPTH) / 2);
        break;
      case 'ramp':
        slot.obj.position.set(x, 0, z);
        break;
    }
  }

  private createPu(kind: PowerUpKind): PuSlot {
    const obj = new Group();
    const item = new Mesh(this.assets.powerUps[kind], this.assets.matProps);
    item.castShadow = true;
    const sc = PICKUP_SCALE[kind] ?? 1;
    item.scale.setScalar(sc);
    const halo = new Mesh(this.assets.halo, this.assets.matHalo[kind]);
    halo.renderOrder = 6;
    obj.add(halo, item);
    this.scene.add(obj);
    return { obj, item, halo, kind, stamp: 0 };
  }

  private syncCollectibles(s: WorldSnapshot): void {
    const st = this.stamp;
    const d = s.distance;
    const items = s.collectibles;
    const t = this.time;
    let n = 0;
    const dm = this.dummy;
    for (let i = 0; i < items.length; i++) {
      const c = items[i];
      const z = -(c.s - d);
      if (c.kind === 'coin') {
        if (n >= MAX_COINS || z > CAMERA.posZ + COIN_BEHIND_CULL) continue;
        dm.position.set(c.x, c.y, z);
        dm.rotation.set(0, t * 4 + c.id * 0.35, 0);
        dm.updateMatrix();
        this.coins.setMatrixAt(n++, dm.matrix);
        continue;
      }
      const kind: CollectibleKind = c.kind;
      let slot = this.puMap.get(c.id);
      if (slot && slot.kind !== kind) {
        this.releasePuCb(slot);
        slot = undefined;
      }
      if (!slot) {
        slot = this.puPools[kind].pop() ?? this.createPu(kind);
        this.puMap.set(c.id, slot);
      }
      slot.stamp = st;
      slot.obj.visible = true;
      slot.obj.position.set(c.x, c.y + 0.12 * Math.sin(t * 3 + c.id), z);
      slot.item.rotation.set(kind === 'hoverboard' ? 0.5 : 0, t * 2.4, kind === 'sneakers' ? 0.25 : 0);
      slot.halo.quaternion.copy(this.camera.quaternion);
      const pulse = 1 + 0.1 * Math.sin(t * 6 + c.id);
      slot.halo.scale.set(pulse, pulse, pulse);
    }
    this.coins.count = n;
    this.coins.instanceMatrix.needsUpdate = true;
    this.puMap.forEach(this.sweepPu);
  }

  // ---------------------------------------------------------------------------
  private updateCamera(s: WorldSnapshot, dt: number): void {
    const p = s.player;
    const narrow = this.aspect < 1.05;
    const fx = narrow ? CAMERA.followXNarrow : CAMERA.followX;
    const tx = p.x * fx;
    const fly = this.fly;
    const ty = CAMERA.posY + p.y * CAMERA.followY + fly * 0.2;
    const ly = CAMERA.lookY + p.y * CAMERA.followY + fly * 0.9;
    if (this.first) {
      this.camX = tx;
      this.camY = ty;
      this.lookY = ly;
      this.first = false;
    } else {
      this.camX = damp(this.camX, tx, CAMERA.dampX, dt);
      this.camY = damp(this.camY, ty, CAMERA.dampY, dt);
      this.lookY = damp(this.lookY, ly, CAMERA.dampLookY, dt);
    }
    this.fovKick = damp(this.fovKick, 0, 2.5, dt);
    this.shake = Math.max(0, this.shake - dt * 1.8);
    const calm = reducedMotion(); // prefers-reduced-motion: no shake, rumble or FOV kicks
    const sh = calm ? 0 : this.shake * this.shake;
    const t = this.time;
    const over = this.overdrive(s);
    const rumble = calm ? 0 : over * 0.025;
    const sx = sh * 0.45 * Math.sin(t * 57.3) + rumble * Math.sin(t * 93.7);
    const sy = sh * 0.35 * Math.sin(t * 71.1 + 1.3) + rumble * Math.sin(t * 81.3 + 0.7);

    const base =
      lerp(CAMERA.fovMin, CAMERA.fovMax, clamp(s.speedLevel, 0, 1)) + over * 9 + fly * 1.5 - this.slow * 3 + (calm ? 0 : this.fovKick);
    let v = base;
    if (this.aspect < CAMERA.refAspect) {
      const h = 2 * Math.atan(Math.tan((base * DEG) / 2) * CAMERA.refAspect);
      v = Math.min(CAMERA.maxFov, (2 * Math.atan(Math.tan(h / 2) / this.aspect)) / DEG);
    }
    this.fov = v;
    const cam = this.camera;
    const back = (narrow ? 0.6 : 0) + fly * 0.5;
    cam.position.set(this.camX + sx, this.camY + sy, CAMERA.posZ + back);
    cam.lookAt(this.camX + sx * 0.5, this.lookY + sy * 0.5, CAMERA.lookZ);
    if (cam.fov !== v || cam.aspect !== this.aspect) {
      cam.fov = v;
      cam.aspect = this.aspect;
      cam.updateProjectionMatrix();
    }

    this.sky.position.copy(cam.position);
    this.clouds.position.set(cam.position.x * 0.9 + Math.sin(t * 0.01) * 40, 0, cam.position.z);
    this.sun.position.set(this.camX + 10, 24, 12);
    this.sun.target.position.set(this.camX, 0, -8);
  }

  dispose(): void {
    this.particles.dispose();
    this.speedLines.dispose();
    this.tint.dispose();
    this.runner.dispose();
    this.coins.dispose();
    this.sun.shadow.map?.dispose();
  }
}
