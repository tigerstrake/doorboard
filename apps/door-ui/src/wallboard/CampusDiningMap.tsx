import { useMemo } from "react";
import {
  CAMPUS_ASPECT,
  DINING_HALLS,
  DOORBOARD_AT,
  HOOVER_TOWER,
  hallFromTitle,
  projectToCampus,
  resolveDiningHall,
  streetPath,
} from "./campusMap";
import type { DiningHall } from "./campusMap";
import {
  CAMPUS_STREETS,
  CAMPUS_WATER,
  OSM_ATTRIBUTION,
  STREET_COORD_SCALE,
  STREET_LAYER_ORDER,
} from "./campusStreets";

/**
 * The recommended dining hall, on a street map of campus.
 *
 * "Wilbur — dinner" tells you nothing about whether that is a two-minute walk or a fifteen.
 *
 * The streets are real OpenStreetMap geometry, baked into the bundle at build time
 * (`scripts/build-campus-streets`) rather than fetched — the doorboard has to work with the
 * internet down (E-19), and a wallboard is not where you want to discover that a tile server
 * is unreachable. The first version of this panel drew three rectangles and a blob, which
 * read as an abstract diagram of nowhere in particular.
 *
 * Drawn in the doorboard's own language: dark field, blue lines, the accent spent only on
 * the thing being pointed at. Road classes carry different weights, which is what makes a
 * tangle of lines resolve into a map — footpaths as hairlines for texture, then service
 * roads, then the through-roads brightest.
 */

/** Exported so a test can check where a marker landed without restating the numbers. */
export const MAP_WIDTH = 520;
export const MAP_HEIGHT = Math.round(MAP_WIDTH / CAMPUS_ASPECT);
const WIDTH = MAP_WIDTH;
const HEIGHT = MAP_HEIGHT;

export function CampusDiningMap({
  hall,
  title,
  backupHall,
}: {
  /** The structured hall from the payload (ADR-0026); may be absent on older events. */
  hall: string | null | undefined;
  /** Prose fallback of the form "{hall} — {meal}", for events predating `hall`. */
  title?: string | null;
  backupHall?: string | null;
}) {
  const resolved = useMemo(
    () => resolveDiningHall(hall) ?? resolveDiningHall(hallFromTitle(title)),
    [hall, title]
  );
  const backup = useMemo(() => resolveDiningHall(backupHall), [backupHall]);

  // One concatenated path per road class instead of one element per way: 2000+ ways would be
  // 2000+ DOM nodes on a screen that stays up for weeks, and they all share a colour and a
  // weight. Constant geometry, so this runs once for the life of the page.
  const streets = useMemo(
    () =>
      STREET_LAYER_ORDER.map((layer) => ({
        layer,
        d: streetPath(CAMPUS_STREETS[layer], STREET_COORD_SCALE, WIDTH, HEIGHT),
      })).filter((entry) => entry.d.length > 0),
    []
  );

  const water = useMemo(
    () => streetPath(CAMPUS_WATER, STREET_COORD_SCALE, WIDTH, HEIGHT),
    []
  );

  const project = (at: { lat: number; lng: number }) => projectToCampus(at, WIDTH, HEIGHT);
  const doorboard = project(DOORBOARD_AT);
  const hoover = project(HOOVER_TOWER);
  const pickAt = resolved ? project(resolved.at) : null;

  return (
    <div className="campus-map" data-testid="campus-dining-map">
      <svg
        className="campus-map__svg"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={
          resolved
            ? `Street map of campus showing ${resolved.name} dining hall`
            : "Street map of campus; the recommended dining hall could not be located"
        }
      >
        {/*
          Water first: paths cross it (the dam) and should draw over the top. Real outlines —
          the previous version inscribed an ellipse in Lake Lagunita's bounding box, which
          next to real streets read as a blob dropped onto the map.
        */}
        {water ? <path className="campus-map__water" d={water} /> : null}

        {streets.map(({ layer, d }) => (
          <path key={layer} className={`campus-map__street campus-map__street--${layer}`} d={d} />
        ))}

        {/* The walk, as the crow flies. Not a route — nothing here knows the paths. */}
        {pickAt ? (
          <line
            className="campus-map__walk"
            x1={doorboard.x}
            y1={doorboard.y}
            x2={pickAt.x}
            y2={pickAt.y}
          />
        ) : null}

        {DINING_HALLS.map((diningHall) => {
          const at = project(diningHall.at);
          const isPick = resolved?.name === diningHall.name;
          const isBackup = !isPick && backup?.name === diningHall.name;
          return (
            <g
              key={diningHall.name}
              className={[
                "campus-map__hall",
                isPick ? "campus-map__hall--pick" : "",
                isBackup ? "campus-map__hall--backup" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              data-testid={isPick ? "campus-map-pick" : undefined}
            >
              {isPick ? (
                <circle className="campus-map__pick-glow" cx={at.x} cy={at.y} r={13} />
              ) : null}
              <circle className="campus-map__dot" cx={at.x} cy={at.y} r={isPick ? 5 : 3} />
              {isPick || isBackup ? (
                <text className="campus-map__hall-label" x={at.x} y={at.y - 11}>
                  {diningHall.name}
                </text>
              ) : null}
            </g>
          );
        })}

        <g className="campus-map__here">
          <circle className="campus-map__here-dot" cx={doorboard.x} cy={doorboard.y} r={3.5} />
          <text className="campus-map__here-label" x={doorboard.x} y={doorboard.y - 9}>
            you are here
          </text>
        </g>

        <g className="campus-map__tower">
          <circle className="campus-map__tower-dot" cx={hoover.x} cy={hoover.y} r={2} />
          <text
            className="campus-map__landmark-label campus-map__landmark-label--start"
            x={hoover.x + 6}
            y={hoover.y + 3}
          >
            Hoover
          </text>
        </g>

        <g className="campus-map__legend">
          <circle className="campus-map__dot" cx={11} cy={HEIGHT - 11} r={3} />
          <text
            className="campus-map__landmark-label campus-map__landmark-label--start"
            x={20}
            y={HEIGHT - 8}
          >
            other dining halls
          </text>
        </g>

        {/* ODbL requires the attribution wherever the data is shown. */}
        <text
          className="campus-map__attribution"
          x={WIDTH - 6}
          y={HEIGHT - 8}
          textAnchor="end"
        >
          {OSM_ATTRIBUTION}
        </text>
      </svg>

      {resolved ? (
        <p className="campus-map__note">
          {resolved.name}
          {resolved.approximate ? " — the dining room is inside this building" : ""}
        </p>
      ) : (
        <p className="campus-map__note placeholder-subtext">
          {/* Honest failure: the recommendation still shows, the map just does not guess. */}
          Not on the map — this hall is not in the campus catalogue.
        </p>
      )}
    </div>
  );
}

export type { DiningHall };
