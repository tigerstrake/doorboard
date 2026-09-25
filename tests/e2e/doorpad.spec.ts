import { expect, test, type Page } from "@playwright/test";

// The shape `GET /doorpad/video-message/latest` returns: a playback URL and a duration,
// never a storage path or a consent field (the doorpad states visitor_initiated itself).
type LatestRecording = {
  recording_id: string;
  playback_url: string;
  duration_s: number | null;
  finalized_at_utc: string | null;
};

type InboxRecording = LatestRecording & {
  consent_context: "visitor_initiated" | "bell_event" | null;
  thumbnail_path: string | null;
};

// door-api's origin as the kiosk derives it from the page it is served on (apiBase.ts):
// this suite is served from 127.0.0.1:5173, so door-api is 127.0.0.1:8080.
const API = "http://127.0.0.1:8080";

test.beforeEach(async ({ page }) => {
  await installMockDoorApi(page);
});

/**
 * The admin token is held in memory only (ADR-0042: the console can open on the corridor
 * touchscreen), so every visit to /admin goes through the sign-in form. The mock answers
 * the admin routes regardless of the bearer value.
 */
async function signInToAdmin(page: Page) {
  await page.goto("/admin");
  await page.getByLabel("Admin token").fill("e2e-admin-token");
  await page.getByRole("button", { name: "Continue" }).click();
}

test("visitor records, reviews, saves, and admin inbox shows metadata", async ({ page }) => {
  await page.goto("/doorpad");

  await page.getByRole("button", { name: "Video Message" }).click();
  await page.getByRole("button", { name: "Start Recording" }).click();
  await expect(page.getByText("Recording", { exact: false })).toBeVisible({ timeout: 5_000 });

  await page.getByRole("button", { name: "Stop & Review" }).click();
  await expect(page.getByRole("heading", { name: "Review Message" })).toBeVisible();
  await expect(page.getByText("Consent context: visitor_initiated")).toBeVisible();
  // The preview arrives from the review-step poll, not from the Stop button.
  await expect(page.locator("video.review-video")).toHaveAttribute("src", "data:video/mp4;base64,");

  await page.getByRole("button", { name: "Save Message" }).click();
  await expect(page.getByRole("heading", { name: "Message Saved" })).toBeVisible();

  await signInToAdmin(page);
  await expect(page.getByRole("heading", { name: "Video Message Inbox" })).toBeVisible();
  await expect(page.getByText("Consent: visitor_initiated")).toBeVisible();
  await expect(page.getByText("3.5 seconds", { exact: false })).toBeVisible();
});

test("discarded reviewed message leaves the inbox empty", async ({ page }) => {
  await page.goto("/doorpad");

  await page.getByRole("button", { name: "Video Message" }).click();
  await page.getByRole("button", { name: "Start Recording" }).click();
  await expect(page.getByText("Recording", { exact: false })).toBeVisible({ timeout: 5_000 });

  await page.getByRole("button", { name: "Stop & Review" }).click();
  await expect(page.getByRole("heading", { name: "Review Message" })).toBeVisible();
  await page.getByRole("button", { name: "Discard" }).click();

  // Discarding is leaving: the visitor lands on the home tiles, not the offer screen.
  await expect(page.getByRole("button", { name: "Video Message" })).toBeVisible();
  await signInToAdmin(page);
  await expect(page.getByText("No saved visitor video messages.")).toBeVisible();
});

test("QR handoff fetches a visitor token", async ({ page }) => {
  await page.goto("/doorpad");

  await page.getByRole("button", { name: "Video Message" }).click();
  await page.getByRole("button", { name: "Open Visitor QR" }).click();

  await expect(page.getByRole("heading", { name: "Visitor Link" })).toBeVisible();
  await expect(page.getByText("door.local/visitor?token=", { exact: false })).toBeVisible();
});

async function installMockDoorApi(page: Page) {
  let sessionState = "IDLE";
  let latestRecording: LatestRecording | null = null;
  let inbox: InboxRecording[] = [];

  const snapshot = () => ({
    session: { state: sessionState, session_id: "018f6a61-0000-7000-8000-000000000402" },
    config: { max_recording_s: 5 },
  });

  await page.route(`${API}/session`, async (route) => {
    await route.fulfill({ json: snapshot() });
  });

  await page.route(`${API}/doorpad/ring`, async (route) => {
    sessionState = "VISITOR_MODE";
    await route.fulfill({ json: { accepted: true, effect: { status: "unavailable" }, ...snapshot() } });
  });

  await page.route(`${API}/doorpad/video-message/offer`, async (route) => {
    sessionState = "VIDEO_MESSAGE_OFFERED";
    await route.fulfill({ json: { accepted: true, effect: { status: "unavailable" }, ...snapshot() } });
  });

  await page.route(`${API}/doorpad/video-message/start`, async (route) => {
    sessionState = "VIDEO_MESSAGE_RECORDING";
    latestRecording = null;
    await route.fulfill({ json: { accepted: true, ...snapshot() } });
  });

  await page.route(`${API}/doorpad/video-message/stop`, async (route) => {
    sessionState = "VIDEO_MESSAGE_REVIEW";
    latestRecording = {
      recording_id: "018f6a61-0000-7000-8000-000000000999",
      playback_url: "data:video/mp4;base64,",
      duration_s: 3.5,
      finalized_at_utc: "2026-09-25T10:00:04Z",
    };
    await route.fulfill({ json: { accepted: true, ...snapshot() } });
  });

  await page.route(`${API}/doorpad/video-message/latest`, async (route) => {
    await route.fulfill({ json: { recording: latestRecording } });
  });

  await page.route(`${API}/doorpad/video-message/save`, async (route) => {
    sessionState = "VIDEO_MESSAGE_SAVED";
    if (latestRecording) {
      inbox = [
        {
          ...latestRecording,
          consent_context: "visitor_initiated",
          thumbnail_path: "thumbnails/mock.jpg",
        },
      ];
    }
    await route.fulfill({ json: { accepted: true, ...snapshot() } });
  });

  await page.route(`${API}/doorpad/video-message/discard`, async (route) => {
    sessionState = "IDLE";
    latestRecording = null;
    inbox = [];
    await route.fulfill({ json: { accepted: true, ...snapshot() } });
  });

  await page.route(`${API}/visitor-token`, async (route) => {
    await route.fulfill({
      json: {
        token: "mock-token",
        url: "http://door.local/visitor?token=mock-token",
        expires_at: 1_800_000_000,
      },
    });
  });

  await page.route(`${API}/admin/media-inbox`, async (route) => {
    await route.fulfill({ json: { recordings: inbox } });
  });
}
