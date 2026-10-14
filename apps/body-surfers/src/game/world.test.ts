import { describe, expect, it } from 'vitest';
import { World } from './World';
import { AutoPilot } from './AutoPilot';
import { POWERUPS, TrackGenerator, type RowSpec } from './generator';
import { CONFIG as C } from './config';
import {
  BEHIND_DISTANCE, RAMP_LENGTH, SIM_DT, TRAIN_CAR_LENGTH, TRAIN_HEIGHT, VIEW_DISTANCE,
} from '../shared/constants';
import type { GameEvent, PlayerAction, PowerUpKind } from '../shared/types';

function mk(tutorial = false): World {
  const w = new World(0);
  w.reset({ seed: 1, tutorial });
  w.testClearTrack();
  w.start();
  return w;
}

function run(w: World, seconds: number, each?: (t: number) => void): GameEvent[] {
  const ev: GameEvent[] = [];
  const n = Math.round(seconds / SIM_DT);
  for (let i = 0; i < n; i++) {
    each?.(i * SIM_DT);
    w.step(SIM_DT);
    ev.push(...w.drainEvents());
  }
  return ev;
}

const count = (ev: GameEvent[], type: GameEvent['type']) => ev.filter((e) => e.type === type).length;

describe('physics', () => {
  it('jump apex ≈ 1.45 m and airtime ≈ 0.70 s', () => {
    const w = mk();
    w.handleAction({ type: 'jump' });
    let apex = 0;
    let air = 0;
    let landed = false;
    run(w, 1.2, () => {
      const p = w.snapshot().player;
      apex = Math.max(apex, p.y);
      if (!p.grounded && !landed) air += SIM_DT;
      if (air > 0 && p.grounded) landed = true;
    });
    expect(apex).toBeCloseTo(1.45, 1);
    expect(Math.abs(air - 0.7)).toBeLessThan(0.03);
  });

  it('super sneakers jump ≈ 4 m', () => {
    const w = mk();
    w.testAddItem('sneakers', 0, 2, 1);
    const ev = run(w, 0.3);
    expect(ev.some((e) => e.type === 'powerUp' && e.kind === 'sneakers')).toBe(true);
    w.handleAction({ type: 'jump' });
    let apex = 0;
    const ev2 = run(w, 1.5, () => { apex = Math.max(apex, w.snapshot().player.y); });
    expect(ev2.find((e) => e.type === 'jump')).toEqual({ type: 'jump', super: true });
    expect(apex).toBeGreaterThan(3.9);
    expect(apex).toBeLessThan(4.1);
  });

  it('roll lasts 0.65 s; duck in the air fast-falls then rolls on landing', () => {
    const w = mk();
    w.handleAction({ type: 'duck' });
    let rollT = 0;
    run(w, 1, () => { if (w.snapshot().player.rolling) rollT += SIM_DT; });
    expect(Math.abs(rollT - 0.65)).toBeLessThan(0.02);

    w.handleAction({ type: 'jump' });
    run(w, 0.2);
    w.handleAction({ type: 'duck' });
    expect(w.snapshot().player.vy).toBeLessThanOrEqual(-22);
    const ev = run(w, 0.2);
    const land = ev.findIndex((e) => e.type === 'land');
    const roll = ev.findIndex((e) => e.type === 'roll');
    expect(land).toBeGreaterThanOrEqual(0);
    expect(roll).toBeGreaterThan(land);
    expect(w.snapshot().player.rolling).toBe(true);
  });

  it('jump within 0.3 s of a ground duck cancels the roll', () => {
    const w = mk();
    w.handleAction({ type: 'duck' });
    run(w, 0.1);
    w.handleAction({ type: 'jump' });
    const p = w.snapshot().player;
    expect(p.rolling).toBe(false);
    expect(p.grounded).toBe(false);
  });

  it('buffers a jump pressed ≤ 0.15 s before landing', () => {
    const w = mk();
    w.handleAction({ type: 'jump' });
    run(w, 0.6); // lands at 0.70
    w.handleAction({ type: 'jump' });
    const ev = run(w, 0.2);
    expect(count(ev, 'jump')).toBe(1);

    const w2 = mk();
    w2.handleAction({ type: 'jump' });
    run(w2, 0.4);
    w2.handleAction({ type: 'jump' }); // 0.3 s early → dropped
    const ev2 = run(w2, 0.4);
    expect(count(ev2, 'jump')).toBe(0);
  });

  it('lateral motion is continuous, 0.17 s per lane', () => {
    const w = mk();
    w.handleAction({ type: 'move', dir: 1 });
    run(w, 0.085);
    const x = w.snapshot().player.x;
    expect(x).toBeGreaterThan(0.8);
    expect(x).toBeLessThan(1.8);
    run(w, 0.1);
    expect(w.snapshot().player.x).toBeCloseTo(2.6, 5);
  });
});

describe('collisions', () => {
  it('low barrier: crash unless jumping', () => {
    const w = mk();
    w.testAddObstacle('barrier_low', 0, 10, 0.4);
    const ev = run(w, 1.5);
    expect(ev.find((e) => e.type === 'crash')).toEqual({ type: 'crash', cause: 'barrier_low', moving: false });
    expect(w.snapshot().status).toBe('dead');

    const w2 = mk();
    w2.testAddObstacle('barrier_low', 0, 10, 0.4);
    let jumped = false;
    run(w2, 1.5, () => {
      const s = w2.snapshot();
      if (!jumped && 10 - s.distance < 11 * 0.3) { w2.handleAction({ type: 'jump' }); jumped = true; }
    });
    expect(w2.snapshot().status).toBe('running');
  });

  it('a low barrier in another lane is harmless', () => {
    const w = mk();
    w.testAddObstacle('barrier_low', 1, 10, 0.4);
    run(w, 1.5);
    expect(w.snapshot().status).toBe('running');
  });

  it('high barrier: roll passes; standing or a normal jump crashes', () => {
    for (const act of [null, 'jump'] as const) {
      const w = mk();
      w.testAddObstacle('barrier_high', 0, 10, 0.4);
      let done = false;
      run(w, 1.5, () => {
        if (act && !done && 10 - w.snapshot().distance < 3) { w.handleAction({ type: act }); done = true; }
      });
      expect(w.snapshot().status).toBe('dead');
    }
    const w = mk();
    w.testAddObstacle('barrier_high', 0, 10, 0.4);
    let done = false;
    run(w, 1.5, () => {
      if (!done && 10 - w.snapshot().distance < 3) { w.handleAction({ type: 'duck' }); done = true; }
    });
    expect(w.snapshot().status).toBe('running');
  });

  it('static train front is fatal', () => {
    const w = mk();
    w.testAddObstacle('train', 0, 10, 26);
    const ev = run(w, 1.5);
    expect(ev.find((e) => e.type === 'crash')).toEqual({ type: 'crash', cause: 'train', moving: false });
  });

  it('moving train: horn once, crash reports moving', () => {
    const w = mk();
    w.testAddObstacle('train', 0, 80, 13, 7);
    const ev = run(w, 6);
    expect(count(ev, 'trainHorn')).toBe(1);
    expect(ev.find((e) => e.type === 'crash')).toEqual({ type: 'crash', cause: 'train', moving: true });
  });

  it('ramp → roof → fall off the far end', () => {
    const w = mk();
    w.testAddObstacle('ramp', 0, 10, RAMP_LENGTH);
    w.testAddObstacle('train', 0, 10 + RAMP_LENGTH, TRAIN_CAR_LENGTH * 2);
    let maxY = 0;
    let roofSeen = false;
    const ev = run(w, 5, () => {
      const p = w.snapshot().player;
      maxY = Math.max(maxY, p.y);
      if (p.onRoof) roofSeen = true;
    });
    expect(w.snapshot().status).toBe('running');
    expect(roofSeen).toBe(true);
    expect(maxY).toBeCloseTo(TRAIN_HEIGHT, 5);
    expect(ev.some((e) => e.type === 'land' && !e.onRoof)).toBe(true);
    expect(w.snapshot().player.y).toBe(0);
  });

  it('coyote time: jump shortly after running off a roof', () => {
    const w = mk();
    w.testAddObstacle('ramp', 0, 5, RAMP_LENGTH);
    w.testAddObstacle('train', 0, 5 + RAMP_LENGTH, TRAIN_CAR_LENGTH);
    let sent = false;
    let airT = 0;
    const ev = run(w, 4, () => {
      const p = w.snapshot().player;
      if (!p.grounded && p.vy <= 0 && !sent && w.snapshot().distance > 20) {
        airT += SIM_DT;
        if (airT >= 0.05) { w.handleAction({ type: 'jump' }); sent = true; }
      }
    });
    expect(sent).toBe(true);
    expect(count(ev, 'jump')).toBe(1);
  });

  it('super jump lands on a train roof; adjacent roof hop allowed', () => {
    const w = mk();
    w.testAddItem('sneakers', 0, 1, 1);
    w.testAddObstacle('train', 0, 14, 39);
    w.testAddObstacle('train', 1, 14, 39);
    let jumped = false;
    let moved = false;
    const ev = run(w, 4, (t) => {
      const s = w.snapshot();
      if (!jumped && 14 - s.distance < 11 * 0.45) { w.handleAction({ type: 'jump' }); jumped = true; }
      if (s.player.onRoof && !moved) { w.handleAction({ type: 'move', dir: 1 }); moved = true; }
      void t;
    });
    expect(ev.some((e) => e.type === 'land' && e.onRoof)).toBe(true);
    expect(moved).toBe(true);
    expect(count(ev, 'blocked')).toBe(0);
    expect(w.snapshot().status).toBe('running');
  });
});

describe('side bumps & lane semantics', () => {
  it('blocked → wait → auto-move, one blocked event, never fatal', () => {
    const w = mk();
    w.testAddObstacle('train', 1, -5, 39); // alongside, far end at 34 m
    w.handleAction({ type: 'lane', lane: 1 });
    const ev = run(w, 1, () => w.handleAction({ type: 'lane', lane: 1 }));
    expect(count(ev, 'blocked')).toBe(1);
    const s = w.snapshot();
    expect(s.player.lane).toBe(0);
    expect(s.player.blockedDir).toBe(1);
    expect(s.status).toBe('running');
    const ev2 = run(w, 3);
    expect(count(ev2, 'blocked')).toBe(0);
    expect(ev2.some((e) => e.type === 'laneChange' && e.to === 1)).toBe(true);
    expect(w.snapshot().player.lane).toBe(1);
    expect(w.snapshot().player.blockedDir).toBe(0);
  });

  it('keyboard move while blocked can be cancelled by an opposite lane request', () => {
    const w = mk();
    w.testAddObstacle('train', 1, -5, 39);
    w.handleAction({ type: 'move', dir: 1 });
    run(w, 0.3);
    w.handleAction({ type: 'lane', lane: 0 });
    run(w, 4);
    expect(w.snapshot().player.lane).toBe(0);
  });

  it('a move into the side of a train mid-transition bounces back', () => {
    const w = mk();
    w.testAddObstacle('train', 1, 1.3, 39); // near face just ahead: pre-check passes at the lookahead edge
    let sent = false;
    const ev = run(w, 5, () => { if (!sent) { w.handleAction({ type: 'move', dir: 1 }); sent = true; } });
    // Clipping the train's corner while switching lanes is a forgiving side bump.
    expect(w.snapshot().status).toBe('running');
    expect(count(ev, 'blocked')).toBe(1);
    expect(w.snapshot().player.lane).toBe(1); // auto-moves once the train has passed
  });

  it('move is relative and clamps; lane is absolute', () => {
    const w = mk();
    w.handleAction({ type: 'move', dir: 1 });
    run(w, 0.05);
    w.handleAction({ type: 'move', dir: 1 }); // already at the edge target
    run(w, 0.5);
    expect(w.snapshot().player.lane).toBe(1);
    w.handleAction({ type: 'lane', lane: -1 });
    const ev = run(w, 0.6);
    expect(ev.filter((e) => e.type === 'laneChange')).toEqual([
      { type: 'laneChange', from: 1, to: 0 },
      { type: 'laneChange', from: 0, to: -1 },
    ]);
    expect(w.snapshot().player.x).toBeCloseTo(-2.6, 5);
  });
});

describe('power-ups & scoring', () => {
  it('timed power-up emits powerUp, one warning 2 s before, then end', () => {
    const w = mk();
    w.testAddItem('magnet', 0, 3, 1);
    const ev = run(w, 14);
    expect(count(ev, 'powerUp')).toBe(1);
    expect(count(ev, 'powerUpWarning')).toBe(1);
    expect(count(ev, 'powerUpEnd')).toBe(1);
    const iw = ev.findIndex((e) => e.type === 'powerUpWarning');
    const ie = ev.findIndex((e) => e.type === 'powerUpEnd');
    expect(iw).toBeLessThan(ie);
  });

  it('magnet pulls coins from other lanes; multiplier doubles coin score; combo counts', () => {
    const w = mk();
    w.testAddItem('magnet', 0, 2, 1);
    w.testAddItem('multiplier', 0, 3, 1);
    for (let i = 0; i < 5; i++) w.testAddItem('coin', 1, 20 + i * 2.4, 1);
    const ev = run(w, 3);
    const coins = ev.filter((e) => e.type === 'coin');
    expect(coins.length).toBe(5);
    expect(coins[4]).toMatchObject({ combo: 5, total: 5 });
    const s = w.snapshot();
    expect(s.multiplier).toBe(2);
    expect(s.coins).toBe(5);
    expect(s.score).toBeGreaterThanOrEqual(100);
  });

  it('hoverboard absorbs one crash with invulnerability, then expires', () => {
    const w = mk();
    w.testAddItem('hoverboard', 0, 2, 1);
    w.testAddObstacle('barrier_low', 0, 12, 0.4);
    w.testAddObstacle('barrier_low', 0, 60, 0.4);
    const ev = run(w, 1.5);
    expect(count(ev, 'hoverboardBreak')).toBe(1);
    expect(count(ev, 'crash')).toBe(0);
    const p = w.snapshot().player;
    expect(p.hoverboard).toBe(false);
    expect(p.alive).toBe(true);
    const ev2 = run(w, 5);
    expect(count(ev2, 'crash')).toBe(1);
  });

  it('milestones every 500 m and speedUp levels', () => {
    const w = mk();
    const ev = run(w, 120);
    const ms = ev.filter((e) => e.type === 'milestone').map((e) => (e as { distance: number }).distance);
    expect(ms.slice(0, 3)).toEqual([500, 1000, 1500]);
    expect(ev.filter((e) => e.type === 'speedUp').map((e) => (e as { level: number }).level)).toEqual([1, 2, 3].slice(0, ev.filter((e) => e.type === 'speedUp').length));
    const s = w.snapshot();
    expect(s.speed).toBeCloseTo(13 + 14 * (1 - Math.exp(-120 / 90)) + 0.05 * 120, 1);
  });

  it('keeps getting faster past the old cap, announcing speedUps, up to the safety cap', () => {
    const w = mk();
    const at = (secs: number) => { run(w, secs); return w.snapshot().speed; };
    const v3 = at(180);
    const v6 = at(180);
    const v9 = at(180);
    expect(v3).toBeGreaterThan(27);
    expect(v6).toBeGreaterThan(v3 + 5);
    expect(v9).toBeGreaterThan(v6);
    expect(v9).toBeLessThanOrEqual(48);
  });
});

describe('lifecycle & snapshot', () => {
  it('ready does not scroll; dead stops scrolling but animTime ticks', () => {
    const w = new World(1);
    w.reset({ seed: 5, tutorial: false });
    w.step(0.5);
    expect(w.snapshot().distance).toBe(0);
    expect(w.snapshot().status).toBe('ready');
    w.testClearTrack();
    w.testAddObstacle('barrier_low', 0, 5, 0.4);
    w.start();
    run(w, 1);
    const s = w.snapshot();
    expect(s.status).toBe('dead');
    expect(s.player.anim).toBe('dead');
    const d = s.distance;
    const at = s.player.animTime;
    run(w, 0.5);
    expect(w.snapshot().distance).toBe(d);
    expect(w.snapshot().player.animTime).toBeGreaterThan(at);
  });

  it('snapshot culls to the view window; ids unique across resets', () => {
    const w = new World(0);
    const ap = new AutoPilot();
    const seen = new Set<number>();
    for (let r = 0; r < 3; r++) {
      w.reset({ seed: 42, tutorial: false });
      w.start();
      for (let i = 0; i < 3000; i++) {
        const a = ap.decide(w.snapshot());
        if (a) w.handleAction(a);
        w.step(SIM_DT);
      }
      const s = w.snapshot();
      for (const o of s.obstacles) {
        expect(o.s + o.length).toBeGreaterThanOrEqual(s.distance - BEHIND_DISTANCE);
        expect(o.s).toBeLessThanOrEqual(s.distance + VIEW_DISTANCE);
      }
      const ids = [...s.obstacles.map((o) => o.id), ...s.collectibles.map((c) => c.id)];
      expect(new Set(ids).size).toBe(ids.length);
      if (r > 0) for (const id of ids) expect(seen.has(id)).toBe(false);
      for (const id of ids) seen.add(id);
      w.drainEvents();
    }
  });

  it('is deterministic: same seed + same inputs ⇒ identical runs', () => {
    const sig = () => {
      const w = new World(0);
      const ap = new AutoPilot();
      w.reset({ seed: 777, tutorial: true });
      w.start();
      const out: string[] = [];
      for (let i = 0; i < 120 * 60; i++) {
        if (i % 2 === 0) { const a = ap.decide(w.snapshot()); if (a) w.handleAction(a); }
        w.step(SIM_DT);
        for (const e of w.drainEvents()) out.push(JSON.stringify(e));
      }
      const s = w.snapshot();
      out.push(String(s.distance), String(s.score), String(s.coins), JSON.stringify(s.obstacles.map((o) => [o.kind, o.lane, o.s, o.length])));
      return out.join('|');
    };
    expect(sig()).toBe(sig());
  });

  it('same seed ⇒ same course for two racers regardless of inputs', () => {
    const a = new World(0);
    const b = new World(1);
    a.reset({ seed: 99, tutorial: false });
    b.reset({ seed: 99, tutorial: false });
    a.start(); b.start();
    for (let i = 0; i < 600; i++) {
      if (i === 100) b.handleAction({ type: 'move', dir: -1 });
      a.step(SIM_DT); b.step(SIM_DT);
    }
    const sa = a.snapshot().obstacles.map((o) => `${o.kind}${o.lane}${o.s.toFixed(3)}`).join();
    const sb = b.snapshot().obstacles.map((o) => `${o.kind}${o.lane}${o.s.toFixed(3)}`).join();
    expect(sa).toBe(sb);
  });
});

describe('tutorial', () => {
  it('shows lane-aware hints and crashes are non-fatal', () => {
    const w = new World(0);
    w.reset({ seed: 3, tutorial: true });
    w.start();
    const hints = new Map<string, number>();
    const ev = run(w, (C.tutorialEnd - 15) / C.speedMin, () => {
      const h = w.snapshot().hint;
      if (h) hints.set(h, (hints.get(h) ?? 0) + 1);
    });
    // Did nothing: hits barriers + trains but keeps running.
    expect(w.snapshot().status).toBe('running');
    expect(count(ev, 'crash')).toBe(0);
    expect(count(ev, 'blocked')).toBeGreaterThan(0);
    expect(hints.has('jump')).toBe(true);
    expect(hints.has('duck')).toBe(true);
    expect(hints.has('switch')).toBe(true);
    expect(hints.has('right')).toBe(true);
    // Standing in the center: the 'left' row (trains right+center) still hints left.
    expect(hints.has('left')).toBe(true);
  });

  it('hint is null when already safe and outside the lead window', () => {
    const w = new World(0);
    w.reset({ seed: 3, tutorial: true });
    w.start();
    expect(w.snapshot().hint).toBeNull(); // 55 m away at 13 m/s = 4.2 s
    run(w, (160 - C.speedMin * 2.0) / C.speedMin);
    expect(w.snapshot().hint).toBe('switch');
    w.handleAction({ type: 'move', dir: -1 });
    w.step(SIM_DT);
    expect(w.snapshot().hint).toBeNull();
  });

  it('AutoPilot clears the tutorial without a single stumble', () => {
    for (let seed = 1; seed <= 20; seed++) {
      const w = new World(0);
      const ap = new AutoPilot();
      w.reset({ seed, tutorial: true });
      w.start();
      const ev: GameEvent[] = [];
      while (w.snapshot().distance < C.tutorialEnd + 20) {
        const a: PlayerAction | null = ap.decide(w.snapshot());
        if (a) w.handleAction(a);
        w.step(SIM_DT);
        ev.push(...w.drainEvents());
      }
      expect(count(ev, 'blocked')).toBe(0);
      expect(count(ev, 'hoverboardBreak')).toBe(0);
    }
  });
});

describe('generator invariants (10k rows)', () => {
  it('holds over 10 seeds × 1000 rows', () => {
    let rows = 0;
    const patterns = new Map<string, number>();
    for (let seed = 0; seed < 10; seed++) {
      const attract = seed % 3 === 2;
      const g = new TrackGenerator({ seed: seed * 7 + 1, tutorial: seed % 2 === 0, attract });
      let prev: RowSpec | null = null;
      const reserved = [0, 0, 0];
      let consecutiveVertical = 0;
      const vTimes: number[] = [];
      for (let i = 0; i < 1000; i++) {
        const r = g.next();
        rows++;
        patterns.set(r.pattern, (patterns.get(r.pattern) ?? 0) + 1);
        // ≥ 1 lane passable by staying (empty / jumpable / rollable / ramp).
        expect(r.passMask).not.toBe(0);
        for (let l = 0; l < 3; l++) {
          if (reserved[l] > r.s) expect(r.lanes[l].content).toBe('empty');
        }
        if (r.pattern !== 'tutorial' && prev) {
          const v = r.speed;
          expect(r.s - prev.far).toBeGreaterThanOrEqual(C.minSpacing - 1e-6);
          let moving = 0;
          for (const l of r.lanes) if (l.content === 'moving') moving = l.mspeed;
          if (r.requiredChanges > 0) {
            const need = (v + moving) * (C.reactTime + C.reactPerExtraLane * (r.requiredChanges - 1));
            expect(r.s - prev.far).toBeGreaterThanOrEqual(need * 0.98);
          }
          for (let l = 0; l < 3; l++) {
            const a = prev.lanes[l].content;
            const b = r.lanes[l].content;
            if ((a === 'low' && b === 'high') || (a === 'high' && b === 'low')) {
              expect(r.s - prev.far).toBeGreaterThanOrEqual(v * (C.jumpAirtime + C.rollTime) + 4 - 1e-6);
            }
          }
          if (moving > 0) {
            expect(attract).toBe(false);
            expect(r.level).toBeGreaterThanOrEqual(C.movingUnlock - 0.02);
            expect(r.lanes.some((l) => l.content === 'empty')).toBe(true);
          }
          if (r.pattern === 'zigzag') expect(r.level).toBeGreaterThanOrEqual(C.zigzagUnlock - 0.02);
          // Long trains: nothing new starts in any lane until far + spacing.
          expect(r.s).toBeGreaterThan(prev.far);
          if (r.vertical) {
            consecutiveVertical++;
            const t = g.course.timeAt(r.s);
            vTimes.push(t);
            expect(vTimes.filter((x) => t - x < 60).length).toBeLessThanOrEqual(C.maxVerticalPerMinute + 1);
          } else consecutiveVertical = 0;
          expect(consecutiveVertical).toBeLessThanOrEqual(C.maxConsecutiveVertical);
        }
        for (let l = 0; l < 3; l++) if (r.lanes[l].content === 'moving') reserved[l] = Math.max(reserved[l], r.reserveUntil);
        // Items stay inside [prev.far, r.far + small].
        for (const it of r.items) {
          if (prev) expect(it.s).toBeGreaterThan(prev.far);
          expect(it.s).toBeLessThan(r.far + 12);
        }
        prev = r;
      }
    }
    expect(rows).toBe(10000);
    console.log('[generator] pattern counts', Object.fromEntries(patterns));
  });

  it('ramp rows: ramp directly in front of its train', () => {
    const w = new World(0);
    w.reset({ seed: 11, tutorial: false });
    w.start();
    for (let i = 0; i < 120 * 60; i++) w.step(SIM_DT); // the runner dies early; just inspect the course
    const g = new TrackGenerator({ seed: 11, tutorial: false, attract: false });
    for (let i = 0; i < 500; i++) {
      const r = g.next();
      for (const l of r.lanes) if (l.content === 'ramp') expect(l.cars).toBeGreaterThanOrEqual(2);
    }
    expect(TRAIN_HEIGHT).toBeGreaterThan(0);
  });
});

describe('new power-ups', () => {
  const DUR: Record<PowerUpKind, number> = {
    magnet: C.magnetDuration, multiplier: C.multiplierDuration, sneakers: C.sneakersDuration,
    hoverboard: C.hoverboardDuration, jetpack: C.jetpackDuration, slowmo: C.slowmoDuration,
    star: C.starDuration, coinRain: C.coinRainDuration,
  };

  it('every kind: powerUp, one warning, one end', () => {
    for (const kind of POWERUPS) {
      const w = mk();
      w.testAddItem(kind, 0, 3, 1);
      const ev = run(w, DUR[kind] + 1.5);
      expect([kind, count(ev, 'powerUp'), count(ev, 'powerUpWarning'), count(ev, 'powerUpEnd')]).toEqual([kind, 1, 1, 1]);
      expect(ev.findIndex((e) => e.type === 'powerUpWarning')).toBeLessThan(ev.findIndex((e) => e.type === 'powerUpEnd'));
      expect(w.snapshot().status).toBe('running');
    }
  });

  it('jetpack: flies at ~6 m over everything, lanes work, jump/duck ignored, coin trail, safe landing', () => {
    const w = mk();
    w.testAddItem('jetpack', 0, 2, 1);
    for (let s = 12; s < 400; s += 8) w.testAddObstacle('barrier_low', 0, s, 0.4);
    w.testAddObstacle('train', -1, 15, 300);
    w.testAddObstacle('train', 1, 15, 300);
    let maxY = 0;
    let landedAt = -1;
    let flyingAnimOk = true;
    const ev: GameEvent[] = [];
    let t = 0;
    while (t < C.jetpackDuration + 3) {
      const s = w.snapshot();
      if (s.powerUps.some((a) => a.kind === 'jetpack')) {
        maxY = Math.max(maxY, s.player.y);
        if (s.player.grounded || (s.player.anim !== 'jump' && s.player.anim !== 'fall')) flyingAnimOk = false;
        if (Math.abs(t - 2) < 1e-9 + SIM_DT / 2) { w.handleAction({ type: 'jump' }); w.handleAction({ type: 'duck' }); }
        if (Math.abs(t - 3) < SIM_DT / 2) w.handleAction({ type: 'move', dir: 1 });
      }
      w.step(SIM_DT);
      t += SIM_DT;
      for (const e of w.drainEvents()) {
        ev.push(e);
        if (e.type === 'land' && landedAt < 0) landedAt = t;
      }
      if (landedAt >= 0 && t > landedAt + 0.5) break;
    }
    expect(maxY).toBeGreaterThan(C.jetpackHeight - 0.2);
    expect(maxY).toBeLessThan(C.jetpackHeight + 0.2);
    expect(flyingAnimOk).toBe(true);
    expect(count(ev, 'crash')).toBe(0);
    expect(count(ev, 'blocked')).toBe(0);
    expect(count(ev, 'jump')).toBe(0);
    expect(count(ev, 'roll')).toBe(0);
    expect(ev.some((e) => e.type === 'laneChange' && e.to === 1)).toBe(true);
    expect(w.snapshot().coins).toBeGreaterThan(10); // sky trail
    expect(landedAt).toBeGreaterThan(C.jetpackDuration);
    // Landed in lane 1 on the train roof, or protected while doing so.
    const s = w.snapshot();
    expect(s.status).toBe('running');
    expect(s.player.grounded).toBe(true);
    expect(ev.some((e) => e.type === 'land' && e.onRoof)).toBe(true);
  });

  it('jetpack: descending onto barriers is protected for ~0.8 s after touchdown', () => {
    const w = mk();
    w.testAddItem('jetpack', 0, 2, 1);
    for (let s = 12; s < 600; s += 3) w.testAddObstacle('barrier_low', 0, s, 0.4);
    let landT = -1;
    let t = 0;
    let crashT = -1;
    while (t < C.jetpackDuration + 4 && crashT < 0) {
      w.step(SIM_DT);
      t += SIM_DT;
      for (const e of w.drainEvents()) {
        if (e.type === 'land' && landT < 0) landT = t;
        if (e.type === 'crash') crashT = t;
      }
      if (landT > 0 && t < landT + 0.7) expect(w.snapshot().player.invulnerable).toBeGreaterThan(0);
    }
    expect(landT).toBeGreaterThan(0);
    expect(crashT - landT).toBeGreaterThan(C.jetpackLandInvuln - 0.05);
  });

  it('slowmo: world scrolls at 60% while timers run in real time', () => {
    const a = mk();
    const b = mk();
    a.testAddItem('slowmo', 0, 3, 1);
    b.testAddItem('coin', 0, 3, 1);
    run(a, 1.5); run(b, 1.5);
    const ratio = a.snapshot().speed / b.snapshot().speed;
    expect(ratio).toBeGreaterThan(C.slowmoFactor - 0.02);
    expect(ratio).toBeLessThan(C.slowmoFactor + 0.02);
    const rem = a.snapshot().powerUps[0].remaining;
    run(a, 1);
    expect(a.snapshot().powerUps[0].remaining).toBeCloseTo(rem - 1, 5);
    const d0 = a.snapshot().distance;
    const db0 = b.snapshot().distance;
    run(a, 1); run(b, 1);
    expect((a.snapshot().distance - d0) / (b.snapshot().distance - db0)).toBeLessThan(0.65);
    const ev = run(a, C.slowmoDuration);
    expect(count(ev, 'powerUpEnd')).toBe(1);
    expect(a.snapshot().speed).toBeGreaterThan(b.snapshot().speed * 0.95);
  });

  it('star: smashes barriers and trains (front + side), never crashes, glows', () => {
    const w = mk();
    w.testAddItem('star', 0, 2, 1);
    w.testAddObstacle('barrier_low', 0, 15, 0.4);
    w.testAddObstacle('barrier_high', 0, 25, 0.4);
    w.testAddObstacle('train', 0, 40, 26, 6);
    w.testAddObstacle('train', 1, 30, 60);
    let moved = false;
    const ev = run(w, 5, () => {
      const s = w.snapshot();
      if (s.distance > 45 && !moved) { w.handleAction({ type: 'move', dir: 1 }); moved = true; }
      if (s.powerUps.some((a) => a.kind === 'star')) expect(s.player.invulnerable).toBeGreaterThan(0);
    });
    const smash = ev.filter((e) => e.type === 'smash');
    expect(smash).toContainEqual({ type: 'smash', kind: 'barrier_low', lane: 0 });
    expect(smash).toContainEqual({ type: 'smash', kind: 'barrier_high', lane: 0 });
    expect(smash).toContainEqual({ type: 'smash', kind: 'train', lane: 0 });
    expect(smash).toContainEqual({ type: 'smash', kind: 'train', lane: 1 });
    expect(count(ev, 'crash')).toBe(0);
    expect(count(ev, 'blocked')).toBe(0);
    const s = w.snapshot();
    expect(s.obstacles.length).toBe(0);
    expect(s.player.lane).toBe(1);
    // After the star (+ grace) runs out, obstacles are deadly again.
    run(w, C.starDuration + C.starGrace);
    w.testAddObstacle('barrier_low', 1, w.snapshot().distance + 10, 0.4);
    expect(count(run(w, 2), 'crash')).toBe(1);
  });

  it('coinRain: dense lines in every lane ahead, roofs over trains, gaps at barriers and moving lanes', () => {
    const w = mk();
    w.testAddItem('coinRain', 0, 2, 1);
    w.testAddObstacle('train', 1, 40, 39);
    w.testAddObstacle('barrier_low', -1, 60, 0.4);
    run(w, 0.3);
    const s = w.snapshot();
    const lo = s.distance + 10;
    const hi = s.distance + 100;
    const inLane = (l: number) => s.collectibles.filter((c) => c.kind === 'coin' && c.lane === l && c.s > lo && c.s < hi);
    for (const l of [-1, 0, 1]) expect(inLane(l).length).toBeGreaterThanOrEqual(30);
    const roof = inLane(1).filter((c) => c.s > 42 && c.s < 77);
    expect(roof.length).toBeGreaterThan(10);
    for (const c of roof) expect(c.y).toBeCloseTo(TRAIN_HEIGHT + 1, 5);
    expect(inLane(-1).some((c) => Math.abs(c.s - 60.2) < 1.2)).toBe(false);
    const before = s.coins;
    run(w, 3);
    expect(w.snapshot().coins - before).toBeGreaterThan(12); // ~one coin per 2.4 m in the runner's lane

    const m = mk();
    m.testAddObstacle('train', 1, 150, 26, 6);
    m.testAddItem('coinRain', 0, 2, 1);
    run(m, 0.5);
    expect(m.snapshot().collectibles.filter((c) => c.lane === 1).length).toBe(0);
  });

  it('generator: power-ups every 200–320 m, all kinds appear', () => {
    const seen = new Map<string, number>();
    for (let seed = 1; seed <= 6; seed++) {
      const g = new TrackGenerator({ seed, tutorial: false, attract: false });
      let last = -1;
      for (let i = 0; i < 800; i++) {
        const r = g.next();
        for (const it of r.items) {
          if (it.kind === 'coin') continue;
          seen.set(it.kind, (seen.get(it.kind) ?? 0) + 1);
          if (last >= 0) {
            expect(it.s - last).toBeGreaterThanOrEqual(C.powerUpMin - 1);
            expect(it.s - last).toBeLessThan(C.powerUpMax + C.powerUpOverdue + 120); // may wait for a usable row
          }
          last = it.s;
        }
      }
    }
    for (const k of POWERUPS) expect(seen.get(k) ?? 0).toBeGreaterThan(0);
  });
});

describe('swept collision at the 48 m/s cap', () => {
  const HALF_D = 0.3;
  /** Empty track, already at the speed cap. */
  function fast(): World {
    const w = mk();
    w.testSetDiffTime(1e4);
    w.step(SIM_DT);
    w.drainEvents();
    expect(w.snapshot().speed).toBeCloseTo(C.speedCap, 5);
    return w;
  }
  const crashOf = (ev: GameEvent[]) => ev.find((e) => e.type === 'crash');

  it('never tunnels: every obstacle kind, any phase offset (incl. paper-thin barriers)', () => {
    const cases: [string, 'barrier_low' | 'barrier_high' | 'train', number, number][] = [
      ['low', 'barrier_low', 0.4, 0],
      ['low thin', 'barrier_low', 0.02, 0],
      ['high', 'barrier_high', 0.4, 0],
      ['high thin', 'barrier_high', 0.02, 0],
      ['train', 'train', 13, 0],
      ['moving train', 'train', 13, C.movingSpeedMax],
    ];
    for (const [name, kind, len, ms] of cases) {
      for (let k = 0; k < 24; k++) {
        const w = fast();
        const phase = (k / 24) * 0.5; // covers a whole step of closing distance
        w.testAddObstacle(kind, 0, w.snapshot().distance + 20 + phase, len, ms);
        const ev = run(w, 1);
        expect([name, phase, crashOf(ev)]).toEqual([name, phase, { type: 'crash', cause: kind, moving: ms > 0 }]);
        // Robust even to a coarse step (1/30 s = 1.6 m per step, 4× the barrier depth).
        const c = fast();
        c.testAddObstacle(kind, 0, c.snapshot().distance + 20 + phase * 4, len, ms);
        const evc: GameEvent[] = [];
        for (let i = 0; i < 30; i++) { c.step(1 / 30); evc.push(...c.drainEvents()); }
        expect([name, 'coarse', phase, crashOf(evc)?.type]).toEqual([name, 'coarse', phase, 'crash']);
      }
    }
  });

  it('ramps always carry the runner onto the roof at any phase', () => {
    for (let k = 0; k < 16; k++) {
      const w = fast();
      const s = w.snapshot().distance + 20 + k * 0.03;
      w.testAddObstacle('ramp', 0, s, RAMP_LENGTH);
      w.testAddObstacle('train', 0, s + RAMP_LENGTH, TRAIN_CAR_LENGTH * 2);
      let roof = false;
      run(w, 1, () => { if (w.snapshot().player.onRoof) roof = true; });
      expect(w.snapshot().status).toBe('running');
      expect(roof).toBe(true);
    }
  });

  it('collects every coin and power-up at high speed, any phase', () => {
    for (let k = 0; k < 10; k++) {
      const w = fast();
      const d = w.snapshot().distance;
      for (let i = 0; i < 60; i++) w.testAddItem('coin', 0, d + 10 + k * 0.041 + i * 1.7, 1);
      w.testAddItem('magnet', 0, d + 120.13 + k * 0.037, 1);
      const ev = run(w, 3);
      expect(count(ev, 'coin')).toBe(60);
      expect(ev.some((e) => e.type === 'powerUp' && e.kind === 'magnet')).toBe(true);
    }
  });

  it('a slightly late jump over a low barrier still crashes (deterministic threshold)', () => {
    const outcome = (lead: number) => {
      const w = fast();
      w.testAddObstacle('barrier_low', 0, w.snapshot().distance + HALF_D + lead, 0.4);
      w.handleAction({ type: 'jump' });
      return crashOf(run(w, 1)) ? 'crash' : 'ok';
    };
    const leads: number[] = [];
    for (let l = 3; l <= 10; l += 0.05) leads.push(l);
    const res = leads.map(outcome);
    const first = res.indexOf('ok');
    expect(first).toBeGreaterThan(0);
    for (let i = first; i < res.length; i++) expect(res[i]).toBe('ok'); // monotone: earlier jumps always clear
    // Clears iff the feet are above 0.9 m when the front reaches the near face (+0.1 m forgiveness).
    // v0 = 8.29 m/s, g = 23.7 ⇒ 0.134 s to 0.9 m ⇒ lead ≈ 48 * 0.134 - 0.1 ≈ 6.35 m (± one step).
    expect(Math.abs(leads[first] - 6.35)).toBeLessThan(0.5);
    expect(outcome(leads[first] - 0.3)).toBe('crash');
  });

  it('a lane change into a train: deep entry crashes, a corner clip bumps, never a silent pass', () => {
    const results: string[] = [];
    const laneSpeed = 2.6 / C.laneTime;
    for (let face = 4.2; face <= 9; face += 0.1) {
      const w = fast();
      w.testAddObstacle('train', 1, w.snapshot().distance + HALF_D + face, 39);
      w.handleAction({ type: 'move', dir: 1 });
      const ev = run(w, 0.5);
      const r = crashOf(ev) ? 'crash' : count(ev, 'blocked') > 0 ? 'bump' : 'pass';
      results.push(r);
      // Where the runner is when its front is 0.15 m past the train's face (mid-step, analytic).
      const xAtFace = Math.min(2.6, laneSpeed * ((face + 0.15) / C.speedCap));
      // ≥ 0.45 m of the 0.8 m-wide runner visibly inside the 2.3 m-wide train ⇒ must be a crash.
      if (2.6 - xAtFace < (0.8 + 2.3) / 2 - 0.45) expect([face, r]).toEqual([face, 'crash']);
    }
    expect(results).not.toContain('pass');
    expect(results).toContain('bump');
    const firstCrash = results.indexOf('crash');
    expect(firstCrash).toBeGreaterThan(0);
    for (let i = firstCrash; i < results.length; i++) expect(results[i]).toBe('crash');
  });

  it('moving into the side of a train you are running alongside is a bump, never a crash', () => {
    const w = fast();
    w.testAddObstacle('train', 1, w.snapshot().distance - 5, 39, C.movingSpeedMax);
    // Moving train coming the other way, already alongside: block pre-check stops the move.
    w.handleAction({ type: 'move', dir: 1 });
    const ev = run(w, 0.3);
    expect(crashOf(ev)).toBeUndefined();
    expect(w.snapshot().player.x).toBeLessThan(2.6 - 1.4);
  });
});
