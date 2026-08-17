import { useEffect, useMemo, useRef, useState } from "react";
import type { AmbientSatellitePassPayload } from "@doorboard/contracts";
import { StatusBadge } from "@doorboard/ui-kit";
import {
  COMPASS_POINTS,
  humanizeSeconds,
  passProgress,
  positionAtFraction,
  projectToSvg,
  trackPath,
} from "./skyDome";
import type { TrackSample } from "./skyDome";

/**
 * The next visible pass, drawn as a sky dome with the satellite moving along its arc.
 *
 * The flights channel plots aircraft on a ground map, which is right for aircraft and wrong
 * for a satellite: a pass is a direction to look, not a position to find. So this is the
 * conventional az/el polar plot — horizon at the rim, zenith at the centre, north up —
 * which is the picture you would actually hold over your head.
 *
 * The arc comes from the provider's sampled track (ADR-0025). It was always computable
 * there and was being discarded in favour of a single compass letter, so the wallboard
 * could say "NW, 64°" and not where to look or for how long.
 *
 * Nothing is invented. With no track the dome shows the culmination point alone and says
 * so, rather than drawing a plausible arc through it — the panel must not assert a shape it
 * was not given.
 */

const SIZE = 320;
const PADDING = 26;
const ELEVATION_RINGS = [30, 60] as const;

export function SatelliteSkyPanel({ payload }: { payload: AmbientSatellitePassPayload }) {
  const riseAtMs = useMemo(() => new Date(payload.rise_at).getTime(), [payload.rise_at]);
  const setAtMs = useMemo(
    () => (payload.set_at ? new Date(payload.set_at).getTime() : null),
    [payload.set_at]
  );
  const track = (payload.track ?? []) as TrackSample[];
  const hasArc = track.length >= 2;

  const [nowMs, setNowMs] = useState(() => Date.now());
  // Drives both the live countdown and, during a pass, the satellite's real position.
  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const progress = passProgress(riseAtMs, setAtMs, nowMs);

  // Before and after a pass the marker previews the arc on a loop, so the panel shows the
  // shape of the pass rather than a dot parked at the horizon. During the pass it tracks
  // the real elapsed fraction instead — an ambient screen should not animate a fiction
  // over a satellite that is genuinely overhead.
  const previewFraction = usePreviewLoop(hasArc && progress.phase !== "during");
  const markerFraction = progress.phase === "during" ? progress.fraction : previewFraction;

  const marker = hasArc
    ? positionAtFraction(track, markerFraction)
    : culminationPoint(payload);

  const path = trackPath(track, SIZE, PADDING);
  const risePoint = hasArc ? track[0]! : null;
  const setPoint = hasArc ? track[track.length - 1]! : null;

  const riseClock = new Date(payload.rise_at).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  const maxElevation = Number.isFinite(payload.max_elevation_deg)
    ? Math.round(payload.max_elevation_deg)
    : null;
  const durationS = setAtMs !== null ? Math.round((setAtMs - riseAtMs) / 1000) : null;

  return (
    <div className="satellite-focus satellite-sky" data-testid="satellite-sky">
      <header className="satellite-focus__head">
        <p className="surface-eyebrow">
          {progress.phase === "during" ? "Overhead now" : "Next visible pass"}
        </p>
        <strong className="satellite-focus__name">{payload.satellite}</strong>
        <p className="satellite-focus__meta">
          {payload.direction} · {maxElevation ?? payload.max_elevation_deg}° max
        </p>
        <StatusBadge label={payload.visible ? "available" : "unknown"} />
      </header>

      <div className="satellite-sky__body">
        <svg
          className="sky-dome"
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          role="img"
          aria-label={`Sky path for ${payload.satellite}, rising ${payload.direction}, maximum elevation ${maxElevation ?? "unknown"} degrees`}
          data-testid="sky-dome"
        >
          <circle className="sky-dome__horizon" cx={SIZE / 2} cy={SIZE / 2} r={SIZE / 2 - PADDING} />
          {ELEVATION_RINGS.map((elevation) => (
            <circle
              key={elevation}
              className="sky-dome__ring"
              cx={SIZE / 2}
              cy={SIZE / 2}
              r={(SIZE / 2 - PADDING) * (1 - elevation / 90)}
            />
          ))}
          {COMPASS_POINTS.map(({ label, azimuth }) => {
            const at = projectToSvg(azimuth, 0, SIZE, PADDING - 14);
            return (
              <text key={label} className="sky-dome__compass" x={at.x} y={at.y}>
                {label}
              </text>
            );
          })}

          {path ? (
            <>
              <path className="sky-dome__arc" d={path} />
              {/* The travelled portion, so a pass in progress reads at a glance. */}
              {progress.phase === "during" ? (
                <path className="sky-dome__arc-done" d={path} pathLength={1} />
              ) : null}
            </>
          ) : null}

          {risePoint ? (
            <circle
              className="sky-dome__endpoint"
              {...toCxCy(risePoint)}
              r={4}
              data-testid="sky-dome-rise"
            />
          ) : null}
          {setPoint ? (
            <circle className="sky-dome__endpoint sky-dome__endpoint--set" {...toCxCy(setPoint)} r={4} />
          ) : null}

          {marker ? (
            <g className="sky-dome__marker" data-testid="sky-dome-marker">
              <circle className="sky-dome__marker-glow" {...toCxCy(marker)} r={11} />
              <circle className="sky-dome__marker-dot" {...toCxCy(marker)} r={4.5} />
            </g>
          ) : null}
        </svg>

        <dl className="satellite-focus__stats">
          <div className="satellite-focus__stat satellite-focus__stat--hero">
            <dt>{progress.phase === "during" ? "Sets" : "Rises"}</dt>
            <dd>
              {progress.phase === "during"
                ? humanizeSeconds(progress.secondsUntilSet)
                : riseClock}
            </dd>
          </div>
          {progress.phase === "before" ? (
            <div className="satellite-focus__stat">
              <dt>Starts</dt>
              <dd>{humanizeSeconds(progress.secondsUntilRise)}</dd>
            </div>
          ) : null}
          <div className="satellite-focus__stat">
            <dt>Look</dt>
            <dd>{payload.direction}</dd>
          </div>
          <div className="satellite-focus__stat">
            <dt>Max elevation</dt>
            <dd>{maxElevation ?? payload.max_elevation_deg}°</dd>
          </div>
          {durationS !== null ? (
            <div className="satellite-focus__stat">
              <dt>Visible for</dt>
              <dd>{Math.floor(durationS / 60)}m {durationS % 60}s</dd>
            </div>
          ) : null}
        </dl>
      </div>

      {!hasArc ? (
        <p className="satellite-sky__note placeholder-subtext">
          Showing the high point only — this pass arrived without a track.
        </p>
      ) : null}
    </div>
  );
}

/** With no sampled arc, the one direction we genuinely know is the culmination. */
function culminationPoint(payload: AmbientSatellitePassPayload): TrackSample | null {
  const azimuth = payload.culmination_azimuth_deg;
  if (azimuth === null || azimuth === undefined) return null;
  return { t_offset_s: 0, azimuth_deg: azimuth, elevation_deg: payload.max_elevation_deg };
}

function toCxCy(sample: TrackSample): { cx: number; cy: number } {
  const { x, y } = projectToSvg(sample.azimuth_deg, sample.elevation_deg, SIZE, PADDING);
  return { cx: x, cy: y };
}

/**
 * A 0→1 ramp on a loop, for previewing the arc when no pass is in progress.
 *
 * requestAnimationFrame rather than an interval, and it stops entirely when disabled: this
 * runs on a wallboard for weeks, so an animation nobody is looking at should cost nothing.
 */
function usePreviewLoop(enabled: boolean): number {
  const [fraction, setFraction] = useState(0);
  const frame = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled) {
      setFraction(0);
      return;
    }
    const PERIOD_MS = 6000;
    const started = performance.now();
    const step = (time: number) => {
      setFraction(((time - started) % PERIOD_MS) / PERIOD_MS);
      frame.current = window.requestAnimationFrame(step);
    };
    frame.current = window.requestAnimationFrame(step);
    return () => {
      if (frame.current !== null) window.cancelAnimationFrame(frame.current);
    };
  }, [enabled]);

  return fraction;
}
