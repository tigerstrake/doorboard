import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type {
  AircraftObserver,
  AmbientAircraftNearby,
  AmbientAircraftSummaryPayload,
} from "@doorboard/contracts";

// Fallback observer location (Stanford) used when the payload omits `observer`
// — keeps the map centred on the door rather than [0,0] in the ocean.
const STANFORD_OBSERVER: AircraftObserver = { latitude: 37.4275, longitude: -122.1697 };

// CARTO dark raster tiles match the wallboard's dark theme. If the display is
// offline the tiles simply fail to load and we degrade to the dark backdrop +
// plotted markers (see `tilesFailed` handling below).
const TILE_URL = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
const TILE_ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> © <a href="https://carto.com/attributions">CARTO</a>';

const finiteNum = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const cleanStr = (value: unknown): string | null => {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
};

const HTML_ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};
const escapeHtml = (value: string): string =>
  value.replace(/[&<>"']/g, (char) => HTML_ESCAPES[char] ?? char);

function verticalTrend(fpm: number | null): { symbol: string; label: string } | null {
  if (fpm === null) return null;
  if (fpm > 100) return { symbol: "↑", label: "climbing" };
  if (fpm < -100) return { symbol: "↓", label: "descending" };
  return { symbol: "→", label: "level" };
}

const hasCoords = (plane: AmbientAircraftNearby): boolean =>
  finiteNum(plane.latitude) !== null && finiteNum(plane.longitude) !== null;

interface FlightsMapProps {
  planes: AmbientAircraftNearby[];
  observer: AircraftObserver;
}

/**
 * Leaflet raster map of nearby aircraft. The map instance is created exactly
 * once (empty-deps effect) and torn down on unmount; marker updates reuse a
 * single LayerGroup so a new `ambient.aircraft_summary` event (~every 30s)
 * re-plots planes without recreating the map. Every Leaflet call is guarded so
 * a headless/offline environment (jsdom in tests, no internet on the Dell)
 * degrades to a "map unavailable" note instead of crashing the 24/7 wallboard.
 */
function FlightsMap({ planes, observer }: FlightsMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [tilesFailed, setTilesFailed] = useState(false);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let timeoutId = 0;
    let resizeObserver: ResizeObserver | null = null;
    try {
      const map = L.map(container, {
        zoomControl: false,
        attributionControl: true,
        // The wallboard is a passive display — disable all interaction so a
        // hallway bump can't pan the map away from the plotted planes.
        dragging: false,
        scrollWheelZoom: false,
        doubleClickZoom: false,
        boxZoom: false,
        keyboard: false,
        touchZoom: false,
      }).setView([observer.latitude, observer.longitude], 9);

      const tiles = L.tileLayer(TILE_URL, {
        attribution: TILE_ATTRIBUTION,
        subdomains: "abcd",
        maxZoom: 19,
      });
      tiles.on("tileerror", () => setTilesFailed(true));
      tiles.addTo(map);

      const layer = L.layerGroup().addTo(map);
      mapRef.current = map;
      layerRef.current = layer;
      setStatus("ready");

      // The focus panel animates in with a CSS transform, so the container's
      // real pixel box isn't stable until the animation settles. Recompute the
      // map size afterwards and on any resize to avoid grey/mis-tiled output.
      const invalidate = () => {
        try {
          mapRef.current?.invalidateSize();
        } catch {
          /* map torn down mid-callback */
        }
      };
      timeoutId = window.setTimeout(invalidate, 480);
      if (typeof ResizeObserver !== "undefined") {
        resizeObserver = new ResizeObserver(invalidate);
        resizeObserver.observe(container);
      }
    } catch {
      setStatus("error");
    }

    return () => {
      if (timeoutId) window.clearTimeout(timeoutId);
      resizeObserver?.disconnect();
      try {
        mapRef.current?.remove();
      } catch {
        /* already removed */
      }
      mapRef.current = null;
      layerRef.current = null;
    };
    // Created once; `observer` only seeds the initial view and live updates are
    // handled by the marker effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) return;

    try {
      layer.clearLayers();

      // Observer (the door) — a labelled pulse ring.
      L.marker([observer.latitude, observer.longitude], {
        icon: L.divIcon({
          className: "flights-observer-icon",
          html: '<span class="flights-observer__ring"></span><span class="flights-observer__dot"></span>',
          iconSize: [26, 26],
          iconAnchor: [13, 13],
        }),
        interactive: false,
        keyboard: false,
      }).addTo(layer);

      const points: L.LatLngExpression[] = [[observer.latitude, observer.longitude]];

      for (const plane of planes) {
        const lat = finiteNum(plane.latitude);
        const lng = finiteNum(plane.longitude);
        if (lat === null || lng === null) continue;
        const heading = finiteNum(plane.heading) ?? 0;
        const callsign = cleanStr(plane.callsign) ?? "—";
        L.marker([lat, lng], {
          icon: L.divIcon({
            className: "flight-marker-icon",
            html:
              `<span class="flight-marker__glyph" style="transform: rotate(${heading}deg)">▲</span>` +
              `<span class="flight-marker__label">${escapeHtml(callsign)}</span>`,
            iconSize: [46, 46],
            iconAnchor: [23, 23],
          }),
          interactive: false,
          keyboard: false,
        }).addTo(layer);
        points.push([lat, lng]);
      }

      if (points.length > 1) {
        map.fitBounds(L.latLngBounds(points), { padding: [46, 46], maxZoom: 11 });
      } else {
        map.setView([observer.latitude, observer.longitude], 9);
      }
    } catch {
      /* ignore transient Leaflet errors (e.g. during teardown) */
    }
  }, [planes, observer]);

  const note =
    status === "error"
      ? "Live map unavailable"
      : tilesFailed
        ? "Map tiles offline · showing positions only"
        : null;

  return (
    <div className="flights-map-wrap">
      <div
        ref={containerRef}
        className="flights-map"
        data-testid="flights-map"
        role="img"
        aria-label="Live map of nearby aircraft"
      />
      {note && (
        <p className="flights-map-note" role="status">
          {note}
        </p>
      )}
    </div>
  );
}

function FlightCard({ plane }: { plane: AmbientAircraftNearby }) {
  const callsign = cleanStr(plane.callsign) ?? "Aircraft";
  const registration = cleanStr(plane.registration);
  const operator = cleanStr(plane.operator);
  const aircraftType = cleanStr(plane.aircraft_type);
  const origin = cleanStr(plane.origin);
  const destination = cleanStr(plane.destination);
  const speed = finiteNum(plane.ground_speed_kmh);
  const altitude = finiteNum(plane.altitude_ft);
  const distance = finiteNum(plane.distance_km);
  const heading = finiteNum(plane.heading);
  const trend = verticalTrend(finiteNum(plane.vertical_rate_fpm));
  const photo = cleanStr(plane.photo_url);
  const photoIsUrl = photo !== null && /^https?:\/\//.test(photo);
  const attribution = cleanStr(plane.photo_attribution);

  const metrics: Array<{ label: string; value: string }> = [];
  if (speed !== null) metrics.push({ label: "Speed", value: `${Math.round(speed).toLocaleString()} km/h` });
  if (altitude !== null) metrics.push({ label: "Altitude", value: `${altitude.toLocaleString()} ft` });
  if (distance !== null) metrics.push({ label: "Distance", value: `${distance.toFixed(1)} km` });
  if (heading !== null) metrics.push({ label: "Heading", value: `${Math.round(heading)}°` });
  if (trend) metrics.push({ label: "Trend", value: `${trend.symbol} ${trend.label}` });

  return (
    <article className="flight-card" data-testid="flight-card">
      {photoIsUrl && (
        <figure className="flight-card__photo">
          <img
            src={photo as string}
            alt={`${callsign} aircraft`}
            loading="lazy"
            onError={(event) => {
              const fig = event.currentTarget.closest("figure");
              if (fig instanceof HTMLElement) fig.style.display = "none";
            }}
          />
          {attribution && <figcaption>{attribution}</figcaption>}
        </figure>
      )}
      <div className="flight-card__body">
        <header className="flight-card__head">
          <strong className="flight-card__callsign">{callsign}</strong>
          {registration && <span className="flight-card__reg">{registration}</span>}
          {operator && <span className="flight-card__operator">{operator}</span>}
        </header>
        {(aircraftType || origin || destination) && (
          <div className="flight-card__sub">
            {aircraftType && <span className="flight-card__type">{aircraftType}</span>}
            {(origin || destination) && (
              <span className="flight-card__route">
                {origin ?? "?"} <span aria-hidden="true">→</span> {destination ?? "?"}
              </span>
            )}
          </div>
        )}
        {metrics.length > 0 && (
          <dl className="flight-card__metrics">
            {metrics.map((metric) => (
              <div className="flight-card__metric" key={metric.label}>
                <dt>{metric.label}</dt>
                <dd>{metric.value}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </article>
  );
}

/**
 * Rich "Flights" focused-panel content: a live Leaflet map of every nearby
 * plane on top, and a scrollable, sparse-tolerant detail list below. Rendered
 * only for the aircraft channel inside the wallboard focus split panel.
 */
export function AircraftFocusPanel({ payload }: { payload: AmbientAircraftSummaryPayload }) {
  const planes = payload.nearby ?? [];
  const observer = payload.observer ?? STANFORD_OBSERVER;
  const plottedCount = planes.filter(hasCoords).length;

  return (
    <div className="flights-focus" data-testid="flights-focus">
      <FlightsMap planes={planes} observer={observer} />
      <div className="flights-list" data-testid="flights-list">
        {planes.length === 0 ? (
          <p className="focus-empty">No nearby aircraft in the latest update.</p>
        ) : (
          <>
            <p className="flights-list__caption">
              {planes.length} nearby · nearest first
              {plottedCount === 0 ? " · positions pending" : ""}
            </p>
            {planes.map((plane, index) => (
              <FlightCard
                key={`${cleanStr(plane.icao24) ?? cleanStr(plane.callsign) ?? "plane"}-${index}`}
                plane={plane}
              />
            ))}
          </>
        )}
      </div>
    </div>
  );
}
