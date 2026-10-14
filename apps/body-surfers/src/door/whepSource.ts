// CameraSource over door-media's WHEP stream (the same player the kiosk's live
// panels use), so the game never opens a camera device of its own.
import { createWhepPlayer, type WhepPlayerOptions } from '@doorboard/media-client';
import type { CameraSource, OpenedCamera } from '../vision/cameraSource';
import { DOOR, DOOR_CAMERA_LABEL } from './config';

export interface PlayerLike {
  start(): void;
  stop(): void;
}

export interface WhepSourceOptions {
  /** door-media's GET /streams. */
  metadataUrl: string;
  streamName: string;
  timeoutMs?: number;
  createPlayer?: (options: WhepPlayerOptions) => PlayerLike;
}

export function whepCameraSource(opts: WhepSourceOptions): CameraSource {
  const create = opts.createPlayer ?? createWhepPlayer;
  const timeoutMs = opts.timeoutMs ?? DOOR.cameraTimeoutS * 1000;
  return {
    label: DOOR_CAMERA_LABEL,
    open(onLost) {
      return new Promise<OpenedCamera>((resolve, reject) => {
        let live: MediaStream | null = null;
        let released = false;
        let lastError: string | null = null;
        let player: PlayerLike | null = null;
        const release = (): void => {
          if (released) return;
          released = true;
          clearTimeout(timer);
          player?.stop();
        };
        const timer = setTimeout(() => {
          if (live || released) return;
          release();
          reject(new Error(lastError ?? 'no video from door-media'));
        }, timeoutMs);
        player = create({
          metadataUrl: opts.metadataUrl,
          streamName: opts.streamName,
          onChange: (snap) => {
            if (released) return;
            if (snap.lastError) lastError = snap.lastError;
            if (!live) {
              if (snap.status === 'connected' && snap.stream) {
                live = snap.stream;
                clearTimeout(timer);
                resolve({ stream: live, release });
              }
              return;
            }
            // The player reconnects by swapping in a new MediaStream (and stops the old
            // tracks without an 'ended' event), so any change of stream means ours is gone.
            if (snap.stream !== live) {
              release();
              onLost();
            }
          },
        });
        player.start();
      });
    },
  };
}
