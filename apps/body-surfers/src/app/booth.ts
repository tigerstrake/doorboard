// Booth display feed: BoothMessages on BroadcastChannel(BROADCAST_CHANNEL) for hall.html.
// 'state' ~4×/s while a run is on screen, 'idle' on entering boot/lobby and every ~2 s
// there, 'result' once per finished run (after leaderboard insertion).
import { BROADCAST_CHANNEL } from '../shared/constants';
import type { BoothMessage, PlayerCount, PlayerResult, Screen, WorldSnapshot } from '../shared/types';
import { APP } from './config';

export interface ChannelLike {
  postMessage(msg: unknown): void;
  close(): void;
}

/** True when a periodic message is due (`last` = time of the previous post, s). */
export function boothDue(now: number, last: number, interval: number): boolean {
  return now - last >= interval;
}

export function stateMessage(screen: Screen, playerCount: PlayerCount, snaps: readonly WorldSnapshot[]): BoothMessage {
  return {
    type: 'state',
    screen,
    playerCount,
    players: snaps.map((s) => ({
      score: Math.floor(s.score),
      coins: s.coins,
      distance: Math.floor(s.distance),
      alive: s.status !== 'dead',
    })),
  };
}

export class BoothBroadcaster {
  private lastState = Number.NEGATIVE_INFINITY;
  private lastIdle = Number.NEGATIVE_INFINITY;

  constructor(private readonly channel: ChannelLike | null = openChannel()) {}

  post(msg: BoothMessage): void {
    if (!this.channel) return;
    try {
      this.channel.postMessage(msg);
    } catch {
      // closed / unclonable — the booth display is optional
    }
  }

  /** Rate-limited 'state' (now in seconds). */
  state(now: number, screen: Screen, playerCount: PlayerCount, snaps: readonly WorldSnapshot[]): void {
    if (!boothDue(now, this.lastState, APP.boothStateS)) return;
    this.lastState = now;
    this.lastIdle = Number.NEGATIVE_INFINITY;
    this.post(stateMessage(screen, playerCount, snaps));
  }

  /** 'idle' every ~2 s; `force` on entering boot/lobby. */
  idle(now: number, force = false): void {
    if (!force && !boothDue(now, this.lastIdle, APP.boothIdleS)) return;
    this.lastIdle = now;
    this.lastState = Number.NEGATIVE_INFINITY;
    this.post({ type: 'idle' });
  }

  result(results: readonly PlayerResult[]): void {
    this.post({ type: 'result', results: results.map((r) => ({ ...r })) });
  }

  dispose(): void {
    try {
      this.channel?.close();
    } catch {
      // ignore
    }
  }
}

function openChannel(): ChannelLike | null {
  try {
    return typeof BroadcastChannel === 'function' ? new BroadcastChannel(BROADCAST_CHANNEL) : null;
  } catch {
    return null;
  }
}
