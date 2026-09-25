import { expect, test } from "@playwright/test";

test("DoorPad post-ring journey fits the 1024x600 kiosk and exposes immediate choices", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "doorpad", "DoorPad viewport coverage");

  await page.route("**/session", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ session: { state: "IDLE", session_id: null }, config: {} }),
    });
  });
  await page.route("**/doorpad/ring", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        accepted: true,
        session: { state: "VISITOR_MODE", session_id: "ses-kiosk-ring" },
        config: {},
      }),
    });
  });
  await page.route("**/door-media/streams", async (route) => {
    await route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
  });

  await page.goto("/doorpad");
  await page.getByRole("button", { name: "Ring Bell" }).click();

  await expect(page.getByText("Bell sent")).toBeVisible();
  await expect(page.getByText("Live view at the door")).toBeVisible();
  await expect(page.getByRole("button", { name: "Wait for Someone to Open" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Send a Video Message" })).toBeVisible();
  await expect(page.getByText("Camera Notice & Deletion Requests")).toHaveCount(0);

  const bounds = await page.locator(".doorpad-waiting-content").boundingBox();
  expect(bounds).not.toBeNull();
  expect((bounds?.y ?? 0) + (bounds?.height ?? 0)).toBeLessThanOrEqual(600);

  await expect(page).toHaveScreenshot("doorpad-post-ring.png", {
    animations: "disabled",
    mask: [page.getByTestId("live-video")],
  });
});

test("DoorPad camera notice and its Details stay inside the 1024x600 panel", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "doorpad", "DoorPad viewport coverage");

  await page.route("**/session", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ session: { state: "IDLE", session_id: null }, config: {} }),
    });
  });

  await page.goto("/doorpad");
  await expect(page.locator("#btn-ring")).toBeVisible();

  // Measured before the fix: the notice sat at y=600..631 on a 600 px panel, with the OS
  // scrollbars hidden so nothing hinted it existed. The panel is a fixed column now.
  const notice = page.locator(".doorpad-camera-notice");
  await expect(notice).toBeVisible();
  const noticeBox = await notice.boundingBox();
  expect(noticeBox).not.toBeNull();
  expect(noticeBox!.y + noticeBox!.height).toBeLessThanOrEqual(600);
  expect(noticeBox!.x + noticeBox!.width).toBeLessThanOrEqual(1024);

  // The toggle is a finger-sized target.
  const toggle = page.getByRole("button", { name: "Details" });
  const toggleBox = await toggle.boundingBox();
  expect(toggleBox!.height).toBeGreaterThanOrEqual(48);
  expect(toggleBox!.width).toBeGreaterThanOrEqual(48);

  // Tapping Details floats the explanation above the row instead of growing the row off
  // the screen (it used to grow the document to 737 px).
  await toggle.click();
  const detail = page.getByTestId("camera-notice-detail");
  await expect(detail).toBeVisible();
  await expect(detail).toContainText(/never controls access/i);
  const detailBox = await detail.boundingBox();
  expect(detailBox!.y).toBeGreaterThanOrEqual(0);
  expect(detailBox!.y + detailBox!.height).toBeLessThanOrEqual(600);
  const noticeAfter = await notice.boundingBox();
  expect(noticeAfter!.y + noticeAfter!.height).toBeLessThanOrEqual(600);
  expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThanOrEqual(600);

  await page.getByRole("button", { name: "Close" }).click();
  await expect(detail).toHaveCount(0);
});

test("recognised visitor + six-option poll keeps Submit and Cancel on the 1024x600 panel", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "doorpad", "DoorPad viewport coverage");

  const options = ["Pizza", "Tacos", "Sushi", "Ramen", "Curry", "Salad"];
  await page.route("**/session", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session: {
          state: "IDLE",
          session_id: null,
          display_name: "Tiger",
          profile_id: "sunrise",
          accent_color: "#00d7a3",
          attributed_to: "Tiger",
          identity_expires_in_s: 90,
        },
        config: {},
      }),
    });
  });
  await page.route("**/polls/current", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        poll: {
          id: "poll-six",
          question: "What should we order tonight?",
          status: "open",
          created_at: "2026-09-25T00:00:00Z",
          closed_at: null,
          options: options.map((text, index) => ({ id: `opt-${index}`, text })),
        },
      }),
    });
  });
  await page.route("**/polls/poll-six/results", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        results: options.map((text, index) => ({ option_id: `opt-${index}`, text, votes: index })),
      }),
    });
  });

  await page.goto("/doorpad");
  await expect(page.getByTestId("doorpad-identity")).toContainText("Tiger");
  await page.locator("#btn-poll").click();

  // The E-23 disclosure is there, before the vote (measured before the fix: with it, six
  // options put Submit at 664 px).
  await expect(page.getByTestId("attribution-notice")).toContainText("Tiger");
  await expect(page.getByRole("button", { name: /Salad/ })).toBeVisible();

  for (const name of ["Submit Vote", "Cancel"]) {
    const box = await page.getByRole("button", { name }).boundingBox();
    expect(box, `${name} should have a box`).not.toBeNull();
    expect(box!.y + box!.height, `${name} bottom edge must be on screen`).toBeLessThanOrEqual(600);
  }
  const overflow = await page.evaluate(() => {
    const view = document.querySelector(".doorpad-view");
    return view ? view.scrollHeight - view.clientHeight : -1;
  });
  expect(overflow).toBe(0);
});

test("phone visitor page has no live-video affordance and confirms poll selection", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "phone-lan", "Phone viewport coverage");

  await page.route("**/visitor-session?token=phone-token", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ session_id: "ses-phone", expires_at: 2_000_000_000, state: "RINGING" }),
    });
  });
  await page.route("**/polls/current", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        poll: {
          id: "poll-phone",
          question: "Tea or coffee?",
          status: "open",
          created_at: "2026-07-14T00:00:00Z",
          closed_at: null,
          options: [
            { id: "tea", text: "Tea" },
            { id: "coffee", text: "Coffee" },
          ],
        },
      }),
    });
  });

  await page.goto("/visitor?token=phone-token");
  await expect(page.getByText("Ringing…")).toBeVisible();
  await expect(page.locator("video, canvas")).toHaveCount(0);
  await expect(page.getByText(/live video/i)).toHaveCount(0);
  const submit = page.getByRole("button", { name: "Submit vote" });
  await expect(submit).toBeDisabled();
  await page.getByRole("button", { name: "Tea" }).click();
  await expect(submit).toBeEnabled();
});
