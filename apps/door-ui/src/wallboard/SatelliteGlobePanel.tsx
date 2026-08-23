import { useEffect, useMemo, useRef, useState } from "react";
import type {
  AmbientSatelliteOrbitsPayload,
  AmbientSatellitePassPayload,
} from "@doorboard/contracts";
import { StatusBadge } from "@doorboard/ui-kit";
import { DOORBOARD_AT } from "./campusMap";
import {
  closestApproach,
  decodeCoastline,
  globePath,
  greatCircleKm,
  groundAtFraction,
  groundTrack,
  orbitAtTime,
  orbitGroundTrack,
  projectToGlobeSvg,
} from "./globe";
import { humanizeSeconds, passProgress } from "./passTiming";
import {
  COASTLINE_COORD_SCALE,
  COASTLINE_CREDIT,
  WORLD_COASTLINE,
} from "./worldCoastline";

/**
 * The next pass, on a globe: where the satellite is now, the ground it covers, and how close
 * it comes to here.
 *
 * The sky dome this replaces answered "which way do I look", which is the right question when
 * a pass is happening and a slightly abstract one the rest of the time. A globe answers
 * "where is the thing" — and since the payload now carries sub-satellite points (ADR-0030),
 * that can be shown truthfully rather than implied from a bearing.
 *
 * Orthographic, centred on the door. Half the sphere is behind it, so every coastline and
 * every track segment is culled at the limb — see globe.ts, where getting that wrong draws
 * Asia across the Pacific and looks perfectly fine.
 */

const SIZE = 340;
const PADDING = 16;
const GRATICULE_STEP = 30;

/**
 * Per-satellite colours for the extra orbits (ADR-0041). Bright against the fixed dark-blue
 * ocean and distinct from the gold used for the highlighted next-pass track, so every loop is
 * legible at corridor distance in either theme. Assigned by index and reused if there are more
 * satellites than colours.
 */
const ORBIT_COLORS = ["#7cc4ff", "#ff8fab", "#8ce99a", "#e599f7", "#ffa94d", "#63e6e2"] as const;

/** "ISS (ZARYA)" → "ISS": the catalogue name minus its parenthetical, for a compact label. */
function shortSatName(name: string): string {
  const trimmed = name.split("(")[0]?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : name;
}

export function SatelliteGlobePanel({
  payload,
  orbits = null,
}: {
  payload: AmbientSatellitePassPayload;
  orbits?: AmbientSatelliteOrbitsPayload | null;
}) {
  const riseAtMs = useMemo(() => new Date(payload.rise_at).getTime(), [payload.rise_at]);
  const setAtMs = useMemo(
    () => (payload.set_at ? new Date(payload.set_at).getTime() : null),
    [payload.set_at]
  );
  const track = useMemo(() => groundTrack(payload.track ?? []), [payload.track]);
  const hasGround = track.length >= 2;

  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const progress = passProgress(riseAtMs, setAtMs, nowMs);

  // During a pass the marker sits at the real elapsed position. Otherwise it sweeps the track
  // on a loop, so the panel shows the shape of the pass rather than a dot parked at one end.
  const preview = usePreviewLoop(hasGround && progress.phase !== "during");
  const fraction = progress.phase === "during" ? progress.fraction : preview;
  const marker = hasGround ? groundAtFraction(track, fraction) : null;

  // Centre on the door, so "how close does it come to us" is read from the middle.
  const centre = DOORBOARD_AT;
  const project = (at: { lat: number; lng: number }) =>
    projectToGlobeSvg(at, centre, SIZE, PADDING);

  const coastline = useMemo(
    () =>
      WORLD_COASTLINE.map((line) =>
        globePath(decodeCoastline(line, COASTLINE_COORD_SCALE), centre, SIZE, PADDING)
      )
        .filter((d) => d.length > 0)
        .join(""),
    [centre]
  );

  const graticule = useMemo(() => {
    const parts: string[] = [];
    for (let lat = -60; lat <= 60; lat += GRATICULE_STEP) {
      const ring: { lat: number; lng: number }[] = [];
      for (let lng = -180; lng <= 180; lng += 5) ring.push({ lat, lng });
      parts.push(globePath(ring, centre, SIZE, PADDING));
    }
    for (let lng = -180; lng < 180; lng += GRATICULE_STEP) {
      const ring: { lat: number; lng: number }[] = [];
      for (let lat = -90; lat <= 90; lat += 5) ring.push({ lat, lng });
      parts.push(globePath(ring, centre, SIZE, PADDING));
    }
    return parts.filter((d) => d.length > 0).join("");
  }, [centre]);

  // Every tracked satellite's whole-orbit ground track (ADR-0041). The paths are static per
  // payload; on an orthographic globe the antimeridian needs no special case — the seam that
  // bites a flat map does not exist here, and globePath already breaks each loop at the limb.
  const orbitRenders = useMemo(() => {
    const list = orbits?.satellites ?? [];
    return list.map((sat, index) => {
      const ground = orbitGroundTrack(sat.track ?? []);
      const color = ORBIT_COLORS[index % ORBIT_COLORS.length]!;
      return {
        name: shortSatName(sat.name),
        color,
        ground,
        path: ground.length >= 2 ? globePath(ground, centre, SIZE, PADDING) : "",
      };
    });
  }, [orbits, centre]);

  // The live markers advance every second: `orbitAtTime` wraps the real clock into each orbit's
  // period, so even an hour-old payload puts each dot where the satellite actually is now.
  const orbitMarkers = orbitRenders.map((orbit) => {
    const at = orbitAtTime(orbit.ground, nowMs);
    const point = at ? project(at) : null;
    return { name: orbit.name, color: orbit.color, point };
  });

  const trackPath = hasGround ? globePath(track, centre, SIZE, PADDING) : "";
  const closest = hasGround ? closestApproach(track, centre) : null;
  const here = project(centre);
  const markerAt = marker ? project(marker) : null;
  const nowKm = marker ? greatCircleKm(marker, centre) : null;

  const riseClock = new Date(payload.rise_at).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  const closestClock =
    closest !== null
      ? new Date(riseAtMs + closest.at.t_offset_s * 1000).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        })
      : null;

  return (
    <div className="satellite-globe" data-testid="satellite-globe">
      <header className="satellite-focus__head">
        <p className="surface-eyebrow">
          {progress.phase === "during" ? "Overhead now" : "Next visible pass"}
        </p>
        <strong className="satellite-focus__name">{payload.satellite}</strong>
        <p className="satellite-focus__meta">
          {payload.direction} · {Math.round(payload.max_elevation_deg)}° max
        </p>
        <StatusBadge label={payload.visible ? "available" : "unknown"} />
      </header>

      <div className="satellite-globe__body">
        <svg
          className="globe"
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          role="img"
          aria-label={`Globe showing ${payload.satellite} and its ground track past this door`}
          data-testid="globe"
        >
          <circle
            className="globe__ocean"
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={SIZE / 2 - PADDING}
          />
          <path className="globe__graticule" d={graticule} />
          <path className="globe__coast" d={coastline} />

          {/* Every other tracked satellite's full-orbit ground track, under the highlighted
              next-pass track so the pass still reads as the primary one. */}
          {orbitRenders.length > 0 ? (
            <g data-testid="globe-orbits">
              {orbitRenders.map((orbit) =>
                orbit.path ? (
                  <path
                    key={orbit.name}
                    d={orbit.path}
                    fill="none"
                    stroke={orbit.color}
                    strokeWidth={1.4}
                    strokeLinejoin="round"
                    opacity={0.8}
                    vectorEffect="non-scaling-stroke"
                  />
                ) : null
              )}
            </g>
          ) : null}

          {trackPath ? <path className="globe__track" d={trackPath} /> : null}

          {/* Where the door is. The globe is centred here, so it is always visible. */}
          <g className="globe__here">
            <circle className="globe__here-ring" cx={here.x} cy={here.y} r={7} />
            <circle className="globe__here-dot" cx={here.x} cy={here.y} r={2.5} />
          </g>

          {markerAt && markerAt.visible ? (
            <g className="globe__sat" data-testid="globe-sat">
              <circle className="globe__sat-ping" cx={markerAt.x} cy={markerAt.y} r={13} />
              <circle className="globe__sat-dot" cx={markerAt.x} cy={markerAt.y} r={4} />
            </g>
          ) : null}

          {/* Each tracked satellite's live position + colour-matched label, on top, and only
              when on the near hemisphere (a far-side dot would be a lie about where it is). */}
          {orbitMarkers.map((orbit) =>
            orbit.point && orbit.point.visible ? (
              <g key={orbit.name} data-testid="globe-orbit-sat">
                <circle cx={orbit.point.x} cy={orbit.point.y} r={3.4} fill={orbit.color} />
                <text
                  x={orbit.point.x + 6}
                  y={orbit.point.y + 3}
                  fontSize={9}
                  fontFamily="var(--db-font-mono)"
                  fill={orbit.color}
                  stroke="rgba(4, 12, 24, 0.75)"
                  strokeWidth={0.7}
                  paintOrder="stroke"
                >
                  {orbit.name}
                </text>
              </g>
            ) : null
          )}
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
          {closest !== null && closestClock !== null ? (
            <div className="satellite-focus__stat">
              {/* A pass does not literally cross a point, so this is the closest approach and
                  its distance — not a claimed intersection. */}
              <dt>Closest to here</dt>
              <dd>
                {Math.round(closest.km).toLocaleString()} km · {closestClock}
              </dd>
            </div>
          ) : null}
          {progress.phase === "during" && nowKm !== null ? (
            <div className="satellite-focus__stat">
              <dt>Distance now</dt>
              <dd>{Math.round(nowKm).toLocaleString()} km</dd>
            </div>
          ) : null}
          <div className="satellite-focus__stat">
            <dt>Look</dt>
            <dd>{payload.direction}</dd>
          </div>
          {/* Kept from the sky-dome panel: a redesign should not quietly drop information the
              old one carried. How high it gets is why a pass is worth watching at all. */}
          <div className="satellite-focus__stat">
            <dt>Max elevation</dt>
            <dd>{Math.round(payload.max_elevation_deg)}°</dd>
          </div>
        </dl>
      </div>

      {orbitRenders.length > 0 ? (
        <div
          data-testid="satellite-orbit-legend"
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "0.4rem 0.9rem",
            justifyContent: "center",
          }}
        >
          {orbitRenders.map((orbit) => (
            <span
              key={orbit.name}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "0.35rem",
                fontFamily: "var(--db-font-mono)",
                fontSize: "0.74rem",
                color: "var(--db-text-secondary)",
              }}
            >
              <span
                aria-hidden="true"
                style={{
                  width: 11,
                  height: 11,
                  borderRadius: 999,
                  background: orbit.color,
                  display: "inline-block",
                }}
              />
              {orbit.name}
            </span>
          ))}
        </div>
      ) : null}

      {hasGround || orbitRenders.length > 0 ? (
        <p className="satellite-globe__credit">Coastline: {COASTLINE_CREDIT}</p>
      ) : (
        <p className="satellite-globe__note placeholder-subtext">
          {/* Honest: without sub-satellite points there is no position to plot, and a bearing
              cannot be turned into one without the orbit. */}
          No ground track in this pass — the globe cannot show where it is.
        </p>
      )}
    </div>
  );
}

/**
 * A 0→1 ramp on a loop, for sweeping the track when no pass is in progress.
 *
 * requestAnimationFrame, and it stops entirely when disabled: this runs on a wallboard for
 * weeks, so an animation nobody is watching should cost nothing.
 */
function usePreviewLoop(enabled: boolean): number {
  const [fraction, setFraction] = useState(0);
  const frame = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled) {
      setFraction(0);
      return;
    }
    const PERIOD_MS = 8000;
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
