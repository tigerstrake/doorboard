// Pure positioning hints (MASTERPLAN §5.8).
import type { Landmark, PositioningHint } from '../shared/types';
import { LM } from '../shared/pose';
import { VISION_CONFIG as C } from './config';

export function computeHints(
  lm: ReadonlyArray<Landmark>,
  centerX: number,
  scale: number,
  confidence: number,
  laneThreshold: number,
  lowFps: boolean,
): PositioningHint[] {
  const out: PositioningHint[] = [];
  const ls = lm[LM.LEFT_SHOULDER];
  const rs = lm[LM.RIGHT_SHOULDER];
  const e = C.hintEdge;
  if (scale > C.hintTooCloseScale || ls.x < e || ls.x > 1 - e || rs.x < e || rs.x > 1 - e) out.push('too-close');
  else if (scale < C.hintTooFarScale) out.push('too-far');
  if (lm[LM.NOSE].y < C.hintHeadCutY) out.push('head-cut-off');
  const reach = C.hintSideLaneFactor * laneThreshold * scale;
  // Not enough room on the screen-left side ⇒ the player should move toward screen-right.
  if (centerX - reach < C.edgeMargin) out.push('move-right');
  else if (centerX + reach > 1 - C.edgeMargin) out.push('move-left');
  if (confidence < C.hintLowConfidence || lowFps) out.push('low-visibility');
  return out;
}
