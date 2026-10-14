// Shared GPU resources (geometries, materials, textures) for every view.
import {
  AdditiveBlending,
  BackSide,
  BufferGeometry,
  Color,
  DoubleSide,
  Material,
  MeshBasicMaterial,
  MeshLambertMaterial,
  MeshToonMaterial,
  Texture,
  TorusGeometry,
} from 'three';
import type { WebGLRenderer } from 'three';
import { PLAYER_COLORS, TRAIN_HEIGHT } from '../shared/constants';
import type { PowerUpKind } from '../shared/types';
import { COLORS, LIVERIES, POWERUP_COLORS } from './config';
import {
  buildBadge,
  buildBarrierHigh,
  buildBarrierLow,
  buildBoard,
  buildClouds,
  buildCoin,
  buildGravel,
  buildCoinCloud,
  buildFlame,
  buildHalo,
  buildHourglass,
  buildJetpack,
  buildStar,
  buildHeadlightGlow,
  buildMagnet,
  buildPeriodic,
  buildRamp,
  buildRunner,
  buildSegment,
  buildSky,
  buildSneaker,
  buildStatic,
  buildTrain,
} from './assets/models';
import type { RunnerGeos, SegmentGeos } from './assets/models';
import { makeFacade, makeGlowSprite, makeGraffiti, makeGravel, makeLivery, makeProps, makeToonRamp } from './assets/textures';

export const SEGMENT_VARIANTS = 6;
export const POWERUP_KINDS: readonly PowerUpKind[] = ['magnet', 'multiplier', 'sneakers', 'hoverboard', 'jetpack', 'slowmo', 'star', 'coinRain'];

export class Assets {
  readonly textures: Texture[] = [];
  readonly materials: Material[] = [];
  readonly geometries: BufferGeometry[] = [];

  readonly matVC: MeshToonMaterial;
  readonly matWall: MeshToonMaterial;
  readonly matFacade: MeshToonMaterial;
  readonly matGround: MeshToonMaterial;
  readonly matProps: MeshToonMaterial;
  readonly matCoin: MeshToonMaterial;
  readonly matLivery: MeshToonMaterial[];
  readonly matHeadlight: MeshBasicMaterial;
  readonly matSky: MeshBasicMaterial;
  readonly matCloud: MeshLambertMaterial;
  readonly matHalo: Record<PowerUpKind, MeshBasicMaterial>;
  readonly matShield: MeshBasicMaterial;
  readonly glowSprite: Texture;

  readonly trains: BufferGeometry[]; // index = cars (1..3); [0] unused alias of 1
  readonly headlight: BufferGeometry;
  readonly barrierLow: BufferGeometry;
  readonly barrierHigh: BufferGeometry;
  readonly ramp: BufferGeometry;
  readonly coin: BufferGeometry;
  readonly powerUps: Record<PowerUpKind, BufferGeometry>;
  readonly halo: BufferGeometry;
  /** Worn jetpack (origin = back attach point, tanks along -z behind the torso). */
  readonly jetpack: BufferGeometry;
  /** Flame cone, base at origin, pointing -y (length 1). */
  readonly flame: BufferGeometry;
  readonly matFlame: MeshBasicMaterial;
  readonly board: BufferGeometry;
  readonly ring: BufferGeometry;
  readonly runners: RunnerGeos[];
  readonly segments: SegmentGeos[];
  readonly gravel: BufferGeometry;
  readonly periodic: BufferGeometry;
  readonly staticEnv: BufferGeometry;
  readonly sky: BufferGeometry;
  readonly clouds: BufferGeometry;

  constructor(renderer: WebGLRenderer) {
    const aniso = Math.min(8, renderer.capabilities.getMaxAnisotropy());
    const ramp = this.t(makeToonRamp());
    const gravelTex = this.t(makeGravel());
    gravelTex.anisotropy = aniso;
    const graffiti = this.t(makeGraffiti());
    const facade = this.t(makeFacade());
    facade.anisotropy = aniso;
    const props = this.t(makeProps());
    this.glowSprite = this.t(makeGlowSprite());

    const toon = (p: ConstructorParameters<typeof MeshToonMaterial>[0]): MeshToonMaterial =>
      this.m(new MeshToonMaterial({ gradientMap: ramp, ...p }));
    this.matVC = toon({ vertexColors: true });
    this.matWall = toon({ map: graffiti });
    this.matFacade = toon({ map: facade, vertexColors: true });
    this.matGround = toon({ map: gravelTex });
    this.matProps = toon({ map: props, vertexColors: true });
    this.matCoin = toon({ map: props, vertexColors: true, emissive: new Color('#7a4a00') });
    this.matLivery = LIVERIES.map((_, i) => toon({ map: this.t(makeLivery(i)), vertexColors: true }));
    this.matHeadlight = this.m(
      new MeshBasicMaterial({
        map: this.glowSprite,
        vertexColors: true,
        transparent: true,
        blending: AdditiveBlending,
        depthWrite: false,
        side: DoubleSide,
      }),
    );
    this.matSky = this.m(new MeshBasicMaterial({ vertexColors: true, side: BackSide, fog: false, depthWrite: false }));
    this.matCloud = this.m(new MeshLambertMaterial({ vertexColors: true, fog: false, emissive: new Color('#9fb8cc'), flatShading: true }));
    const halo = (c: string): MeshBasicMaterial =>
      this.m(
        new MeshBasicMaterial({
          map: this.glowSprite,
          color: c,
          transparent: true,
          opacity: 0.85,
          blending: AdditiveBlending,
          depthWrite: false,
        }),
      );
    this.matHalo = {
      magnet: halo(POWERUP_COLORS.magnet),
      multiplier: halo(POWERUP_COLORS.multiplier),
      sneakers: halo(POWERUP_COLORS.sneakers),
      hoverboard: halo(POWERUP_COLORS.hoverboard),
      jetpack: halo(POWERUP_COLORS.jetpack),
      slowmo: halo(POWERUP_COLORS.slowmo),
      star: halo(POWERUP_COLORS.star),
      coinRain: halo(POWERUP_COLORS.coinRain),
    };
    this.matFlame = this.m(
      new MeshBasicMaterial({ vertexColors: true, transparent: true, blending: AdditiveBlending, depthWrite: false, side: DoubleSide, fog: false }),
    );
    this.matShield = this.m(
      new MeshBasicMaterial({
        color: POWERUP_COLORS.magnet,
        transparent: true,
        opacity: 0.35,
        blending: AdditiveBlending,
        depthWrite: false,
        side: DoubleSide,
      }),
    );

    const one = this.g(buildTrain(1));
    this.trains = [one, one, this.g(buildTrain(2)), this.g(buildTrain(3))];
    this.headlight = this.g(buildHeadlightGlow());
    this.barrierLow = this.g(buildBarrierLow());
    this.barrierHigh = this.g(buildBarrierHigh());
    this.ramp = this.g(buildRamp(TRAIN_HEIGHT));
    this.coin = this.g(buildCoin());
    this.powerUps = {
      magnet: this.g(buildMagnet()),
      multiplier: this.g(buildBadge()),
      sneakers: this.g(buildSneaker()),
      hoverboard: this.g(buildBoard()),
      jetpack: this.g(buildJetpack()),
      slowmo: this.g(buildHourglass()),
      star: this.g(buildStar(0.55, 0.2)),
      coinRain: this.g(buildCoinCloud()),
    };
    this.jetpack = this.powerUps.jetpack;
    this.flame = this.g(buildFlame());
    this.halo = this.g(buildHalo());
    this.board = this.g(buildBoard());
    this.ring = this.g(new TorusGeometry(0.85, 0.035, 6, 40));
    this.runners = PLAYER_COLORS.map((c, i) => {
      const r = buildRunner(c, COLORS.cap[i]);
      for (const k of Object.keys(r) as (keyof RunnerGeos)[]) this.g(r[k]);
      return r;
    });
    this.segments = [];
    for (let v = 0; v < SEGMENT_VARIANTS; v++) {
      const s = buildSegment(v);
      this.g(s.wall);
      this.g(s.facade);
      this.g(s.props);
      this.segments.push(s);
    }
    this.gravel = this.g(buildGravel());
    this.periodic = this.g(buildPeriodic());
    this.staticEnv = this.g(buildStatic());
    this.sky = this.g(buildSky());
    this.clouds = this.g(buildClouds());
  }

  /** Recompile lit materials (after toggling shadows). */
  invalidate(): void {
    for (const m of this.materials) m.needsUpdate = true;
  }

  dispose(): void {
    for (const g of this.geometries) g.dispose();
    for (const m of this.materials) m.dispose();
    for (const t of this.textures) t.dispose();
  }

  private t<T extends Texture>(t: T): T {
    this.textures.push(t);
    return t;
  }
  private m<T extends Material>(m: T): T {
    this.materials.push(m);
    return m;
  }
  private g(g: BufferGeometry): BufferGeometry {
    if (!this.geometries.includes(g)) this.geometries.push(g);
    return g;
  }
}
