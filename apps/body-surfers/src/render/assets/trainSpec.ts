// Train car cross-section + livery texture layout, shared by the texture painter
// and the geometry builder so UVs and paint always line up.
import { TRAIN_CAR_LENGTH, TRAIN_HEIGHT, TRAIN_WIDTH } from '../../shared/constants';
import { pxPoint, pxRect } from '../util/geo';
import type { UVPoint, UVRect } from '../util/geo';

const HW = TRAIN_WIDTH / 2;

export const CAR = {
  halfWidth: HW,
  bodyBottom: 0.55,
  sideTop: 2.78,
  roof: TRAIN_HEIGHT,
  /** Visual inset of the car body from each end of its 13 m slot (matches collision forgiveness). */
  inset: 0.2,
  slot: TRAIN_CAR_LENGTH,
  bodyLength: TRAIN_CAR_LENGTH - 0.4,
} as const;

/** Open polyline from bottom-left, up, over the roof, down to bottom-right. */
export const CAR_PROFILE: readonly (readonly [number, number])[] = [
  [-HW, CAR.bodyBottom],
  [-HW, CAR.sideTop],
  [-HW + 0.08, 3.02],
  [-HW + 0.29, 3.16],
  [-HW + 0.45, TRAIN_HEIGHT],
  [HW - 0.45, TRAIN_HEIGHT],
  [HW - 0.29, 3.16],
  [HW - 0.08, 3.02],
  [HW, CAR.sideTop],
  [HW, CAR.bodyBottom],
];

export const PROFILE_LENGTH = (() => {
  let t = 0;
  for (let i = 0; i + 1 < CAR_PROFILE.length; i++) {
    t += Math.hypot(CAR_PROFILE[i + 1][0] - CAR_PROFILE[i][0], CAR_PROFILE[i + 1][1] - CAR_PROFILE[i][1]);
  }
  return t;
})();

/** Livery canvas layout (px). */
export const LIV = {
  size: 1024,
  /** Side wrap: canvas rows [0, sideRows): row = sideRows*(1 - f) for perimeter fraction f. */
  sideRows: 512,
  front: { x: 0, y: 512, w: 512, h: 512 },
  swatchOrigin: { x: 512, y: 512 },
  swatchSize: 128,
} as const;

/** Perimeter fraction → texture v. */
export const sideV = (f: number): number => 1 - (LIV.sideRows * (1 - f)) / LIV.size;

/** Canvas row for a height y on the LEFT side (f small). */
export const leftRow = (y: number): number => LIV.sideRows * (1 - (y - CAR.bodyBottom) / PROFILE_LENGTH);
/** Canvas row for a height y on the RIGHT side (mirror). */
export const rightRow = (y: number): number => (LIV.sideRows * (y - CAR.bodyBottom)) / PROFILE_LENGTH;

/** Front cap: (x, y) meters → uv in the front rect. */
export function frontUV(x: number, y: number): [number, number] {
  const fx = (x + HW) / TRAIN_WIDTH;
  const fy = (y - CAR.bodyBottom) / (TRAIN_HEIGHT - CAR.bodyBottom);
  const px = LIV.front.x + fx * LIV.front.w;
  const py = LIV.front.y + (1 - fy) * LIV.front.h;
  return [px / LIV.size, 1 - py / LIV.size];
}

/** Front cap: meters → canvas px (for painting). */
export const frontPx = (x: number, y: number): [number, number] => {
  const fx = (x + HW) / TRAIN_WIDTH;
  const fy = (y - CAR.bodyBottom) / (TRAIN_HEIGHT - CAR.bodyBottom);
  return [LIV.front.x + fx * LIV.front.w, LIV.front.y + (1 - fy) * LIV.front.h];
};

export type LiverySwatch =
  | 'white' | 'black' | 'under' | 'chrome'
  | 'glass' | 'roof' | 'body' | 'accent'
  | 'light' | 'red' | 'bodyDark' | 'trim'
  | 'rubber' | 'yellow';

export const SWATCH_CELLS: Record<LiverySwatch, readonly [number, number]> = {
  white: [0, 0], black: [1, 0], under: [2, 0], chrome: [3, 0],
  glass: [0, 1], roof: [1, 1], body: [2, 1], accent: [3, 1],
  light: [0, 2], red: [1, 2], bodyDark: [2, 2], trim: [3, 2],
  rubber: [0, 3], yellow: [1, 3],
};

export function liverySwatch(name: LiverySwatch): UVPoint {
  const [cx, cy] = SWATCH_CELLS[name];
  const s = LIV.swatchSize;
  return pxPoint(LIV.swatchOrigin.x + cx * s + s / 2, LIV.swatchOrigin.y + cy * s + s / 2, LIV.size, LIV.size);
}

/** Side wrap uv rect (whole region) — used by the extrusion's u/v mapping. */
export const SIDE_RECT: UVRect = pxRect(0, 0, LIV.size, LIV.sideRows, LIV.size, LIV.size);

/** Door/window layout along the body (meters from the near end of the body). */
export const SIDE_LAYOUT = {
  doors: [[2.0, 3.5], [9.1, 10.6]] as const,
  windows: [[0.45, 1.6], [4.0, 5.95], [6.65, 8.6], [11.0, 12.15]] as const,
};
