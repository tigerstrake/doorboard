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
