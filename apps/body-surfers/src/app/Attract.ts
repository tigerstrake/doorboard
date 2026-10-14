// Attract mode: an AI runner surfing behind the boot/lobby screens (silent).
import { SIM_DT } from '../shared/constants';
import type { GameEvent, IAutoPilot, IWorld, WorldSnapshot } from '../shared/types';
import { APP } from './config';
import { FixedStepper } from './loop';

export class Attract {
  readonly world: IWorld;
  snapshot: WorldSnapshot;
  events: GameEvent[] = [];
  private pilot: IAutoPilot;
  private deadFor = 0;
  private readonly stepper = new FixedStepper();

  constructor(
    world: IWorld,
    private readonly createPilot: () => IAutoPilot,
    private readonly nextSeed: () => number,
  ) {
    this.world = world;
    this.pilot = createPilot();
    this.snapshot = world.snapshot();
  }

  reset(seed: number = this.nextSeed()): void {
    this.world.reset({ seed, tutorial: false, attract: true });
    this.world.start();
    this.pilot = this.createPilot();
    this.deadFor = 0;
    this.stepper.reset();
    this.world.drainEvents();
    this.snapshot = this.world.snapshot();
  }

  update(dt: number): void {
    const w = this.world;
    const s = w.snapshot();
    if (s.status === 'running') {
      const a = this.pilot.decide(s);
      if (a) w.handleAction(a);
    } else if (s.status === 'dead') {
      this.deadFor += dt;
      if (this.deadFor >= APP.attractResetS) this.reset();
    } else {
      w.start();
    }
    const n = this.stepper.advance(dt);
    for (let k = 0; k < n; k++) w.step(SIM_DT);
    this.snapshot = w.snapshot();
    this.events = w.drainEvents();
  }
}
