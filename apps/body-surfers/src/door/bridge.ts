// postMessage from the game (an iframe) to the doorboard wallboard that hosts it.
// Frame-local: it never leaves the kiosk page, so it is not a packages/contracts
// event. The wallboard mirrors these shapes in apps/door-ui/src/bodySurfers.ts.
// One way only: the wallboard never needs to tell a running game anything. It ends
// a game by unmounting the frame, and clears the leaderboard through clear.html.

export const BRIDGE_CHANNEL = 'body-surfers';

export type DoorExitReason = 'idle' | 'walked_away' | 'error';

type Outgoing = { type: 'ready' } | { type: 'exit'; reason: DoorExitReason } | { type: 'leaderboard-cleared' };

export type GameToDoor = Outgoing & { channel: typeof BRIDGE_CHANNEL };

export class DoorBridge {
  /** `parentOrigin` null (opened standalone, or no valid ?parent=) makes every post a no-op. */
  constructor(
    private readonly win: Window,
    private readonly parentOrigin: string | null,
  ) {}

  post(message: Outgoing): void {
    if (!this.parentOrigin || this.win.parent === this.win) return;
    this.win.parent.postMessage({ channel: BRIDGE_CHANNEL, ...message }, this.parentOrigin);
  }
}
