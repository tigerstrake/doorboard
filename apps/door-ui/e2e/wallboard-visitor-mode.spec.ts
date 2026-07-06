import { test, expect, type Page } from "@playwright/test";
import { publishSessionState, publishIdentityStable, gotoWallboard, screenshotMasks } from "./helpers";

const FORBIDDEN_TEXT = /hailo|sqlite|uptime|diagnostic|ssd space|admin console/i;

async function assertNoPrivacyLeak(page: Page) {
  // No raw camera feed anywhere on a public route, ever.
  await expect(page.locator("video, canvas")).toHaveCount(0);
  const bodyText = await page.locator("body").innerText();
  expect(bodyText).not.toMatch(FORBIDDEN_TEXT);
}

// Freeze Date before the app's first script runs so the ambient clock and the
// inactivity countdown always compute the same elapsed time (zero) — screenshots
// don't race wall-clock-driven UI. Real timers still fire, they just see no
// elapsed time, matching how a kiosk would look at a single instant.
test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(0);
});

const VISITOR_STATES = [
  "BUTTON_PRESSED",
  "VISITOR_MODE",
  "RINGING",
  "ANSWERED",
  "UNANSWERED_TIMEOUT",
  "VIDEO_MESSAGE_OFFERED",
  "VIDEO_MESSAGE_RECORDING",
  "VIDEO_MESSAGE_REVIEW",
  "VIDEO_MESSAGE_SAVED",
  "SESSION_END",
] as const;

test.describe("Wallboard — ambient mode", () => {
  test("renders the ambient dashboard with no visitor-mode leakage", async ({ page }) => {
    await gotoWallboard(page);
    await expect(page.locator(".wallboard-ambient-view")).toBeVisible();
    await expect(page.getByTestId("wallboard-visitor-mode")).toHaveCount(0);
    await assertNoPrivacyLeak(page);

    await expect(page).toHaveScreenshot("ambient.png", {
      animations: "disabled",
      mask: screenshotMasks(page),
    });
  });
});

test.describe("Wallboard — visitor-mode takeover, every session state", () => {
  for (const state of VISITOR_STATES) {
    test(`renders ${state} correctly and leaks no private data`, async ({ page }) => {
      await gotoWallboard(page);
      const sessionId = `ses_${state}`;

      await publishSessionState(page, sessionId, "IDLE", state);

      const takeover = page.getByTestId("wallboard-visitor-mode");
      await expect(takeover).toHaveAttribute("data-session-state", state);
      await expect(page.locator(".wallboard-ambient-view")).toHaveCount(0);

      await assertNoPrivacyLeak(page);

      await expect(page).toHaveScreenshot(`visitor-${state}.png`, {
        animations: "disabled",
        mask: screenshotMasks(page),
      });
    });
  }
});

test.describe("Wallboard — personalization", () => {
  test("shows a generic greeting with no identity", async ({ page }) => {
    await gotoWallboard(page);
    await publishSessionState(page, "ses_generic", "IDLE", "VISITOR_MODE");

    await expect(page.getByTestId("greeting-banner")).toContainText("Hello, Visitor!");
    await expect(page.getByTestId("greeting-banner-sparkles")).toHaveCount(0);
    await assertNoPrivacyLeak(page);
  });

  test("shows an accent-colored, celebratory greeting for an enrolled identity", async ({ page }) => {
    await gotoWallboard(page);
    await publishSessionState(page, "ses_owner", "IDLE", "BUTTON_PRESSED");
    await publishIdentityStable(page, "owner", "Taylor");
    await publishSessionState(page, "ses_owner", "BUTTON_PRESSED", "VISITOR_MODE");

    const banner = page.getByTestId("greeting-banner");
    await expect(banner).toContainText("Welcome back, Taylor!");
    await expect(banner).toHaveClass(/db-greeting-banner--owner/);
    await expect(page.getByTestId("greeting-banner-sparkles")).toHaveCount(1);
    await assertNoPrivacyLeak(page);

    await expect(page).toHaveScreenshot("visitor-personalized-owner.png", {
      animations: "disabled",
      mask: screenshotMasks(page),
    });
  });

  test("late-recognition upgrades the greeting in place without resetting the session", async ({ page }) => {
    await gotoWallboard(page);
    const sessionId = "ses_late_recognition";

    // Generic bell press, no identity yet.
    await publishSessionState(page, sessionId, "IDLE", "VISITOR_MODE");
    await expect(page.getByTestId("greeting-banner")).toContainText("Hello, Visitor!");

    // Ringing starts before anyone is recognized.
    await publishSessionState(page, sessionId, "VISITOR_MODE", "RINGING");
    const ringStatus = page.getByTestId("ring-status");
    await expect(ringStatus).toHaveAttribute("data-state", "RINGING");

    // Identity arrives late, mid-ring.
    await publishIdentityStable(page, "roommate", "Alex");

    // Greeting upgrades in place — the ring status is still there, untouched.
    await expect(page.getByTestId("greeting-banner")).toContainText("Welcome back, Alex!");
    await expect(ringStatus).toHaveAttribute("data-state", "RINGING");
    await expect(page.getByTestId("wallboard-visitor-mode")).toHaveAttribute(
      "data-session-state",
      "RINGING",
    );
  });
});

test.describe("Wallboard — transitions", () => {
  test("returns to ambient after the session ends", async ({ page }) => {
    await gotoWallboard(page);
    const sessionId = "ses_end_to_ambient";

    await publishSessionState(page, sessionId, "IDLE", "VISITOR_MODE");
    await expect(page.getByTestId("wallboard-visitor-mode")).toBeVisible();

    await publishSessionState(page, sessionId, "VISITOR_MODE", "SESSION_END");
    await expect(page.getByTestId("session-end-banner")).toBeVisible();

    await publishSessionState(page, sessionId, "SESSION_END", "IDLE");
    await expect(page.locator(".wallboard-ambient-view")).toBeVisible();
    await expect(page.getByTestId("wallboard-visitor-mode")).toHaveCount(0);
  });
});
