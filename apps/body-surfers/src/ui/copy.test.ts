import { describe, expect, it } from 'vitest';
import { classifyCameraError, friendlyError } from './copy';

describe('classifyCameraError', () => {
  it('treats the real denied-camera message as blocked, not a model load failure', () => {
    const raw =
      "Couldn't start the camera (Camera access was blocked. Click the camera icon in the address bar, allow access, then reload.).";
    expect(classifyCameraError(raw)).toBe('blocked');
    expect(friendlyError(raw).title).toBe('CAMERA BLOCKED');
  });

  it.each([
    ['NotAllowedError: Permission denied', 'blocked'],
    ['SecurityError', 'blocked'],
    ['Camera disconnected', 'disconnected'],
    ['No camera found. Plug in a webcam (or pick another camera in Settings) and try again.', 'missing'],
    ['NotFoundError: Requested device not found', 'missing'],
    ['The camera is busy — close other apps or tabs using it (Zoom, FaceTime, Meet…) and try again.', 'busy'],
    ['NotReadableError: Could not start video source', 'busy'],
    ['Failed to load the pose model', 'model'],
    ['TypeError: Failed to fetch', 'model'],
    ['Something odd', 'unknown'],
  ] as const)('%s -> %s', (raw, kind) => {
    expect(classifyCameraError(raw)).toBe(kind);
  });

  it('never reads "reload" or "loaded" in unrelated text as a load failure', () => {
    expect(classifyCameraError('please reload')).toBe('unknown');
    expect(classifyCameraError('preloaded thing broke')).toBe('unknown');
  });

  it('tips never repeat the body', () => {
    for (const raw of ['denied', 'Camera disconnected', 'busy', 'wasm', '???']) {
      const f = friendlyError(raw);
      expect(f.tips.length).toBeGreaterThan(0);
      expect(f.tips).not.toContain(f.body);
    }
  });
});
