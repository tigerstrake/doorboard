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

// Every focusable channel, with enough data routed in that the real layout renders (the
// Vite e2e server has mock ambient data; guestbook and poll come from door-api).
const FOCUS_CHANNELS = [
  "aircraft",
  "satellite",
  "scoreboard",
  "birds",
  "printer",
  "food",
  "poll",
  "guestbook",
  "moments",
  "about",
] as const;

const POLL = {
  id: "poll-e2e",
  question: "Best late-night study snack on this floor?",
  status: "open",
  created_at: "2026-09-20T00:00:00Z",
  closed_at: null,
  options: [
    { id: "a", text: "Instant noodles" },
    { id: "b", text: "Trail mix" },
    { id: "c", text: "Cold pizza" },
  ],
};
const POLL_RESULTS = [
  { option_id: "a", text: "Instant noodles", votes: 7 },
  { option_id: "b", text: "Trail mix", votes: 3 },
  { option_id: "c", text: "Cold pizza", votes: 5 },
];
const GUESTBOOK = Array.from({ length: 5 }, (_, index) => ({
  id: `gb-${index}`,
  text:
    index % 2 === 0
      ? "Stopped by to say the door is the best thing on this floor — the bird tile made my week."
      : "Call me later about the thing!",
  author_label: index % 2 === 0 ? "A neighbour from 312" : null,
  status: "approved",
  created_at: "2026-09-24T18:00:00Z",
}));

const json = (body: unknown) => ({
  status: 200,
  contentType: "application/json",
  body: JSON.stringify(body),
});

test.describe("Wallboard — every focused channel fits its panel", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/guestbook?*", (route) => route.fulfill(json({ entries: GUESTBOOK })));
    await page.route("**/polls/current", (route) => route.fulfill(json({ poll: POLL })));
    await page.route("**/polls/*/results", (route) => route.fulfill(json({ results: POLL_RESULTS })));
  });

  for (const channel of FOCUS_CHANNELS) {
    test(`${channel}: no clipped content in the focus panel`, async ({ page }) => {
      // A wallboard has no scroll input, so "overflow: auto" is clipping with a decorative
      // scrollbar: the About facts, the satellite legend and the Birds total were all
      // drawn off the panel at 1920x1080 (E2 audit). Runs at both e2e resolutions.
      await gotoWallboard(page);
      if (channel === "poll") {
        await expect(page.locator(".poll-tile-content")).toContainText("study snack");
      }
      if (channel === "guestbook") {
        await expect(page.locator(".guestbook-tile-content")).toContainText(/neighbour|Call me/);
      }
      await publishWallboardFocus(page, channel);
      const panel = page.getByTestId("wallboard-focus-panel");
      await expect(panel).toBeVisible();
      // Let the grow-in animation and any map/globe layout settle.
      await page.waitForTimeout(700);
      const box = await panel.evaluate((el) => ({
        scrollHeight: el.scrollHeight,
        clientHeight: el.clientHeight,
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
      }));
      expect(box.scrollHeight, `vertical overflow ${JSON.stringify(box)}`).toBeLessThanOrEqual(
        box.clientHeight,
      );
      expect(box.scrollWidth, `horizontal overflow ${JSON.stringify(box)}`).toBeLessThanOrEqual(
        box.clientWidth,
      );
    });
  }
});
