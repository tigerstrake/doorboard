import { expect, test, type Page } from "@playwright/test";

type Recording = {
  recording_id: string;
  path: string | null;
  consent_context: "visitor_initiated" | "bell_event" | null;
  thumbnail_path: string | null;
  playback_url?: string;
};

const API = "http://127.0.0.1:8000";

test.beforeEach(async ({ page }) => {
  await installMockDoorApi(page);
});

test("visitor records, reviews, saves, and admin inbox shows metadata", async ({ page }) => {
  await page.goto("/doorpad");

  await page.getByRole("button", { name: "Video Message" }).click();
  await page.getByRole("button", { name: "Start Recording" }).click();
  await expect(page.getByText("Recording", { exact: false })).toBeVisible({ timeout: 5_000 });

  await page.getByRole("button", { name: "Stop & Review" }).click();
  await expect(page.getByRole("heading", { name: "Review Message" })).toBeVisible();
  await expect(page.getByText("Consent context: visitor_initiated")).toBeVisible();

  await page.getByRole("button", { name: "Save Message" }).click();
  await expect(page.getByRole("heading", { name: "Message Saved" })).toBeVisible();

  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "Video Message Inbox" })).toBeVisible();
  await expect(page.getByText("Consent: visitor_initiated")).toBeVisible();
  await expect(page.getByText("Thumbnail ready")).toBeVisible();
});

test("discarded reviewed message leaves the inbox empty", async ({ page }) => {
  await page.goto("/doorpad");

  await page.getByRole("button", { name: "Video Message" }).click();
  await page.getByRole("button", { name: "Start Recording" }).click();
  await expect(page.getByText("Recording", { exact: false })).toBeVisible({ timeout: 5_000 });

  await page.getByRole("button", { name: "Stop & Review" }).click();
  await expect(page.getByRole("heading", { name: "Review Message" })).toBeVisible();
  await page.getByRole("button", { name: "Discard" }).click();

  await expect(page.getByRole("button", { name: "Video Message" })).toBeVisible();
  await page.goto("/admin");
  await expect(page.getByText("No saved visitor video messages.")).toBeVisible();
});

test("QR handoff fetches a visitor token", async ({ page }) => {
  await page.goto("/doorpad");

  await page.getByRole("button", { name: "Video Message" }).click();
  await page.getByRole("button", { name: "Use Phone QR" }).click();

  await expect(page.getByRole("heading", { name: "Continue on Phone" })).toBeVisible();
  await expect(page.getByText("door.local/visitor?token=" , { exact: false })).toBeVisible();
});

async function installMockDoorApi(page: Page) {
  let sessionState = "IDLE";
  let latestRecording: Recording | null = null;
  let inbox: Recording[] = [];

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
      path: "recordings/mock/video_message.mp4",
      consent_context: "visitor_initiated",
      thumbnail_path: "thumbnails/mock.jpg",
      playback_url: "data:video/mp4;base64,",
    };
    await route.fulfill({ json: { accepted: true, ...snapshot() } });
  });

  await page.route(`${API}/doorpad/video-message/latest`, async (route) => {
    await route.fulfill({ json: { recording: latestRecording } });
  });

  await page.route(`${API}/doorpad/video-message/save`, async (route) => {
    sessionState = "VIDEO_MESSAGE_SAVED";
    if (latestRecording) inbox = [latestRecording];
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
