import { expect, test, type Page } from "@playwright/test";

/**
 * The doorpad has to FIT the 7" panel, which is 1024x600 (the project viewport).
 *
 * The reported symptom was "some of the buttons are cut off because the screen is too
 * small". A prior fix had made `.doorpad-view` scrollable, which stops content being
 * clipped but not the confusion: a touchscreen shows no scrollbar, so anything below the
 * fold reads as a missing button. T-316 refit the grid; nothing verified it at the real
 * size, because the panel is on the door and the laptop is not.
 *
 * These assert geometry rather than pixels. A screenshot baseline would fail on a font
 * hint and pass on a button hanging 3px off the bottom, which is the opposite of useful.
 */

const VIEWPORT_HEIGHT = 600;
const VIEWPORT_WIDTH = 1024;

/** Everything the doorpad home screen must offer, by its stable id. */
const HOME_TILE_IDS = [
  "btn-ring",
  "btn-enroll",
  "btn-video",
  "btn-guestbook",
  "btn-poll",
  "btn-checkin",
  "btn-remote",
  "btn-privacy",
];

async function mockIdleSession(page: Page) {
  await page.route("**/session", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ session: { state: "IDLE" }, config: {} }),
    })
  );
  // Everything else the kiosk polls: answer benignly so no screen renders an error
  // state that would change the layout under test.
  await page.route("**/social/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "{}" })
  );
}

async function recogniseSomeone(page: Page, displayName: string) {
  await page.evaluate((name) => {
    new BroadcastChannel("doorboard-events").postMessage({
      event_id: "018f6b2e-7c9a-7000-8000-3f9d2ba41c11",
      type: "vision.identity_stable",
      source: "e2e",
      occurred_at: new Date(0).toISOString(),
      monotonic_ms: 0,
      door_id: "primary",
      trace_id: "9d1e8a34-52a1-4b0e-9c0f-2a7d1f66b100",
      payload: {
        person_id: "prs_e2e",
        display_name: name,
        accent_color: "#00d7a3",
        consent_version: "v3",
        confidence: 0.93,
        expires_at: new Date(Date.now() + 60_000).toISOString(),
        expires_at_monotonic_ms: 60_000,
        profile_id: "warm_amber",
      },
    });
  }, displayName);
}

/**
 * How far the doorpad's own scroll container overflows, in CSS pixels.
 *
 * Measured on `.doorpad-view` rather than the document: that element is the one with
 * `overflow-y: auto`, so it is where content hides. The document-level number is a
 * near-constant 8px of layout slop that says nothing about whether a visitor can see the
 * buttons — checking it instead passed a screen whose content was 70px too tall.
 */
async function doorpadOverflow(page: Page): Promise<number> {
  return page.evaluate(() => {
    const view = document.querySelector(".doorpad-view");
    if (!view) throw new Error(".doorpad-view not found");
    return view.scrollHeight - view.clientHeight;
  });
}

test.beforeEach(async ({ page }) => {
  await mockIdleSession(page);
});

test("every home tile is fully inside the 1024x600 panel", async ({ page }) => {
  await page.goto("/doorpad");
  await expect(page.getByRole("heading", { name: /DoorPad/ })).toBeVisible();

  for (const id of HOME_TILE_IDS) {
    const tile = page.locator(`#${id}`);
    await expect(tile, `${id} should exist`).toBeVisible();
    const box = await tile.boundingBox();
    expect(box, `${id} should have a box`).not.toBeNull();
    // The whole tile, not just its origin: a button whose lower half is off-screen is
    // exactly the reported complaint.
    expect(
      box!.y + box!.height,
      `${id} bottom edge must be on screen (it ends at ${Math.round(box!.y + box!.height)}px)`
    ).toBeLessThanOrEqual(VIEWPORT_HEIGHT);
    expect(box!.x + box!.width, `${id} right edge must be on screen`).toBeLessThanOrEqual(
      VIEWPORT_WIDTH
    );
  }
});

test("the home screen needs no scrolling", async ({ page }) => {
  await page.goto("/doorpad");
  await expect(page.locator("#btn-ring")).toBeVisible();

  // A touchscreen offers no scrollbar hint, so below-the-fold content is invisible
  // rather than merely inconvenient.
  expect(await doorpadOverflow(page)).toBe(0);
});

test("every tile says what it does", async ({ page }) => {
  await page.goto("/doorpad");
  await expect(page.locator("#btn-ring")).toBeVisible();

  for (const id of HOME_TILE_IDS) {
    const hint = page.locator(`#${id} .db-big-button__hint`);
    await expect(hint, `${id} should carry a hint line`).toHaveCount(1);
    expect((await hint.innerText()).trim().length, `${id} hint should not be blank`).toBeGreaterThan(
      0
    );
  }
});

test("the identity badge appears, fits, and takes the person's colour", async ({ page }) => {
  await page.goto("/doorpad");
  await expect(page.locator("#btn-ring")).toBeVisible();
  await expect(page.getByTestId("doorpad-identity")).toHaveCount(0);

  await recogniseSomeone(page, "Tiger");

  const badge = page.getByTestId("doorpad-identity");
  await expect(badge).toBeVisible();
  await expect(badge).toContainText("Tiger");
  const box = await badge.boundingBox();
  expect(box!.x + box!.width).toBeLessThanOrEqual(VIEWPORT_WIDTH);
  expect(box!.y + box!.height).toBeLessThanOrEqual(VIEWPORT_HEIGHT);

  // The accent is applied as a CSS custom property, from the event's accent_color
  // rather than the cataloged profile colour (ADR-0021).
  const accent = await page.evaluate(() => {
    const view = document.querySelector(".doorpad-view") as HTMLElement | null;
    return view ? getComputedStyle(view).getPropertyValue("--db-accent").trim() : "";
  });
  expect(accent.toLowerCase()).toBe("#00d7a3");
});

test("the badge does not shift the title, and the layout returns when it goes", async ({ page }) => {
  await page.goto("/doorpad");
  const title = page.getByRole("heading", { name: /DoorPad/ });
  await expect(title).toBeVisible();
  const before = await title.boundingBox();

  await recogniseSomeone(page, "Tiger");
  await expect(page.getByTestId("doorpad-identity")).toBeVisible();
  const during = await title.boundingBox();

  // The badge was a flex sibling first, which silently re-aligned the centred title the
  // moment it unmounted — the header never went back to normal (T-317).
  expect(Math.round(during!.x)).toBe(Math.round(before!.x));
  expect(Math.round(during!.y)).toBe(Math.round(before!.y));
});

test("the About screen fits without scrolling", async ({ page }) => {
  await page.goto("/doorpad");
  await page.locator("#btn-privacy").click();

  await expect(page.getByTestId("about-doorboard")).toBeVisible();
  // Six sections of prose on a 600px-tall panel: the case most likely to overflow.
  expect(await doorpadOverflow(page)).toBe(0);
});

test("the check-in screen offers the recognised person by name", async ({ page }) => {
  await page.goto("/doorpad");
  await expect(page.locator("#btn-ring")).toBeVisible();
  await recogniseSomeone(page, "Tiger");
  await expect(page.getByTestId("doorpad-identity")).toBeVisible();

  await page.locator("#btn-checkin").click();

  // The original bug: greeted, then offered "Guest" only.
  await expect(page.getByRole("button", { name: "Check in as Tiger" })).toBeVisible();
  expect(await doorpadOverflow(page)).toBe(0);
});
