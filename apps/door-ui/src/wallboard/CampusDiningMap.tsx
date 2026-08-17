import { useMemo } from "react";
import {
  CAMPUS_ASPECT,
  CAMPUS_LANDMARKS,
  DINING_HALLS,
  DOORBOARD_AT,
  HOOVER_TOWER,
  hallFromTitle,
  projectToCampus,
  resolveDiningHall,
} from "./campusMap";
import type { DiningHall } from "./campusMap";

/**
 * The recommended dining hall, placed on a stylised map of campus.
 *
 * "Wilbur — dinner" tells you nothing about whether that is a two-minute walk or a fifteen.
 * The map answers the question the text cannot, using the doorboard's own palette: dark
 * field, blue outlines, the accent reserved for the one thing being recommended.
 *
 * Every other hall is drawn too, dimmed. A single dot on an empty map has no scale — the
 * point of showing the rest is that "the near cluster" and "all the way past the lake" are
 * immediately different pictures.
 */

const WIDTH = 460;
const HEIGHT = Math.round(WIDTH / CAMPUS_ASPECT);

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

  const project = (at: { lat: number; lng: number }) => projectToCampus(at, WIDTH, HEIGHT);
  const doorboard = project(DOORBOARD_AT);
  const hoover = project(HOOVER_TOWER);

  return (
    <div className="campus-map" data-testid="campus-dining-map">
      <svg
        className="campus-map__svg"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={
          resolved
            ? `Map of campus showing ${resolved.name} dining hall`
            : "Map of campus; the recommended dining hall could not be located"
        }
      >
        {CAMPUS_LANDMARKS.map((landmark) => {
          const topLeft = project({ lat: landmark.bounds.north, lng: landmark.bounds.west });
          const bottomRight = project({
            lat: landmark.bounds.south,
            lng: landmark.bounds.east,
          });
          const w = bottomRight.x - topLeft.x;
          const h = bottomRight.y - topLeft.y;
          return landmark.shape === "ellipse" ? (
            <ellipse
              key={landmark.label}
              className="campus-map__water"
              cx={topLeft.x + w / 2}
              cy={topLeft.y + h / 2}
              rx={w / 2}
              ry={h / 2}
            />
          ) : (
            <rect
              key={landmark.label}
              className="campus-map__block"
              x={topLeft.x}
              y={topLeft.y}
              width={w}
              height={h}
              rx={2}
            />
          );
        })}

        {/* The walk, as the crow flies. Not a route — nothing here knows the paths. */}
        {resolved ? (
          <line
            className="campus-map__walk"
            x1={doorboard.x}
            y1={doorboard.y}
            x2={project(resolved.at).x}
            y2={project(resolved.at).y}
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
              {isPick ? <circle className="campus-map__pick-glow" cx={at.x} cy={at.y} r={13} /> : null}
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
          <text className="campus-map__here-label" x={doorboard.x} y={doorboard.y + 15}>
            you are here
          </text>
        </g>

        <text className="campus-map__landmark-label" x={hoover.x + 7} y={hoover.y + 3}>
          Hoover
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
