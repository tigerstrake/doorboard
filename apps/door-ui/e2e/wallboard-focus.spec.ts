import { test, expect } from "@playwright/test";
import { gotoWallboard, publishWallboardFocus } from "./helpers";

// Anything that looks like a map-tile host (matched against the request's host only —
// the dev server's module paths include ui-kit's Tile.tsx). The wallboard must never
// fetch a basemap it does not ship: CARTO began stamping keyless tiles "API KEY REQUIRED"
// and the hallway screen showed the watermark edge to edge, with no error for the old
// fallback to catch.
const TILE_HOST =
  /cartocdn|basemaps|tile\.openstreetmap|(^|\.)tiles?\.|arcgis|mapbox|maptiler|stamen|thunderforest/i;

test.describe("Wallboard — Flights focus", () => {
  test("draws its own basemap and never requests a third-party tile", async ({ page, baseURL }) => {
    const tileRequests: string[] = [];
    page.on("request", (request) => {
      if (TILE_HOST.test(new URL(request.url()).host)) tileRequests.push(request.url());
    });

    await gotoWallboard(page);
    await publishWallboardFocus(page, "aircraft");

    // `L.map(container)` makes the flights-map div itself the Leaflet container.
    const map = page.getByTestId("flights-map");
    await expect(map).toBeVisible();
    await expect(map).toHaveClass(/leaflet-container/);
    // Roads and shoreline are SVG from the bundle; labels and plane markers are DOM.
    await expect(map.locator("svg path").first()).toBeAttached();
    await expect(map.locator(".flights-place").first()).toBeAttached();
    await expect(map.locator(".flight-marker-icon").first()).toBeVisible();
    // ODbL credit, rendered where the data is shown.
    await expect(map.locator(".leaflet-control-attribution")).toContainText("OpenStreetMap");

    // Had a tile layer been added it would have started fetching by now.
    await page.waitForTimeout(1500);
    expect(tileRequests).toEqual([]);

    // Nothing inside the map is an image from anywhere but this origin.
    const origin = new URL(baseURL as string).origin;
    const imageSources = await map
      .locator("img")
      .evaluateAll((imgs) => imgs.map((img) => (img as HTMLImageElement).src));
    expect(imageSources.filter((src) => !src.startsWith(origin))).toEqual([]);
  });
});
