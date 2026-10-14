// Selfie capture (MASTERPLAN §5.9). DOM-only helper.
import type { Landmark } from '../shared/types';
import { LM } from '../shared/pose';
import { VISION_CONFIG as C } from './config';

let canvas: HTMLCanvasElement | null = null;

/**
 * Square JPEG crop around the nose from the live video. `lm` are DISPLAY-space landmarks;
 * `mirror` says whether display space is mirrored relative to the raw video.
 */
export function captureSelfieFromVideo(
  video: HTMLVideoElement,
  lm: ReadonlyArray<Landmark>,
  mirror: boolean,
  size: number,
): string | null {
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  if (!vw || !vh || video.readyState < 2) return null;
  const rx = (x: number) => (mirror ? 1 - x : x) * vw;
  const nose = lm[LM.NOSE];
  const ear = Math.hypot(rx(lm[LM.LEFT_EAR].x) - rx(lm[LM.RIGHT_EAR].x), (lm[LM.LEFT_EAR].y - lm[LM.RIGHT_EAR].y) * vh);
  const sw = Math.hypot(
    rx(lm[LM.LEFT_SHOULDER].x) - rx(lm[LM.RIGHT_SHOULDER].x),
    (lm[LM.LEFT_SHOULDER].y - lm[LM.RIGHT_SHOULDER].y) * vh,
  );
  let side = Math.max(C.selfieEarFactor * ear, C.selfieShoulderFactor * sw);
  if (sw > 0) side = Math.min(side, C.selfieMaxShoulderFactor * sw);
  side = Math.max(48, Math.min(side, vw, vh));
  const cx = rx(nose.x);
  const cy = nose.y * vh - C.selfieShiftUp * side;
  const sx = Math.max(0, Math.min(vw - side, cx - side / 2));
  const sy = Math.max(0, Math.min(vh - side, cy - side / 2));
  if (!canvas) canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.save();
  if (mirror) {
    ctx.translate(size, 0);
    ctx.scale(-1, 1);
  }
  ctx.drawImage(video, sx, sy, side, side, 0, 0, size, size);
  ctx.restore();
  return canvas.toDataURL('image/jpeg', C.selfieQuality);
}
