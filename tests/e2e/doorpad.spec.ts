import { expect, test, type Page } from "@playwright/test";

/**
 * The doorpad's video-message flow and visitor QR handoff, against a mocked door-api.
 *
 * Routes are matched with `**` wildcards rather than a fixed origin: the app derives
 * door-api's base URL from the page it is served on (`${hostname}:8080` unless
 * VITE_DOOR_API_BASE_URL says otherwise), so a mock pinned to one host:port silently
 * intercepts nothing and every assertion below waits on a request that never returns.
 */

type Recording = {
  recording_id: string;
  path: string | null;
  consent_context: "visitor_initiated" | "bell_event" | null;
  thumbnail_path: string | null;
  playback_url?: string;
};

// The key App.tsx reads the /admin bearer from; set before visiting /admin so the
// page loads the inbox instead of showing the token form.
const ADMIN_TOKEN_KEY = "doorboard_admin_social_token";

test.beforeEach(async ({ page }) => {
  await installMockDoorApi(page);
});

/** Video Message tile → Start Recording → past the 3 s countdown into the live recording. */
async function recordUntilLive(page: Page) {
  await page.goto("/doorpad");
  await page.getByRole("button", { name: "Video Message" }).click();
  await expect(page.getByRole("heading", { name: "Leave a Video Message" })).toBeVisible();
  await page.getByRole("button", { name: "Start Recording" }).click();

  // Only the live recording screen renders `.recording-status` with the elapsed/limit
  // counter. A bare `getByText("Recording")` also matches the idle "Start Recording"
  // button and the "Recording Starts In" countdown heading, so it could never fail.
  await expect(page.locator(".recording-status")).toHaveText(/Recording \d+s \/ \d+s/, {
    timeout: 10_000,
  });
  await expect(page.getByRole("button", { name: "Start Recording" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Recording Starts In" })).toHaveCount(0);
}

async function openAdmin(page: Page) {
  await page.evaluate((key) => window.localStorage.setItem(key, "e2e-admin-token"), ADMIN_TOKEN_KEY);
  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "Video Message Inbox" })).toBeVisible();
}

test("visitor records, reviews, saves, and the admin inbox shows the metadata", async ({ page }) => {
  await recordUntilLive(page);

  await page.getByRole("button", { name: "Stop & Review" }).click();
  await expect(page.getByRole("heading", { name: "Review Message" })).toBeVisible();
  await expect(page.getByText("Consent context: visitor_initiated")).toBeVisible();

  // "Save Message" with no configured residents; "Send to <name>" per resident when
  // VITE_RESIDENTS is set. Either one saves.
  await page.getByRole("button", { name: /^(Save Message|Send to )/ }).first().click();
  await expect(page.getByRole("heading", { name: /^(Message Saved|Sent to )/ })).toBeVisible();

  await openAdmin(page);
  await expect(page.getByText("Visitor message", { exact: true })).toBeVisible();
  await expect(page.getByText("Consent: visitor_initiated")).toBeVisible();
});

test("a discarded reviewed message leaves the inbox empty", async ({ page }) => {
  await recordUntilLive(page);

  await page.getByRole("button", { name: "Stop & Review" }).click();
  await expect(page.getByRole("heading", { name: "Review Message" })).toBeVisible();
  await page.getByRole("button", { name: "Discard" }).click();

  // The review is gone and the visitor is back at the offer. door-api answers with an
  // IDLE session, and IDLE deliberately implies no screen (only SESSION_END routes home),
  // so the offer screen with its Cancel button is where the visitor stays.
  await expect(page.getByRole("heading", { name: "Review Message" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Start Recording" })).toBeVisible();

  await openAdmin(page);
  await expect(page.getByText("No saved visitor video messages.")).toBeVisible();
});

test("the visitor QR handoff fetches a visitor token", async ({ page }) => {
  await page.goto("/doorpad");

  await page.getByRole("button", { name: "Video Message" }).click();
  await page.getByRole("button", { name: "Open Visitor QR" }).click();

  await expect(page.getByRole("heading", { name: "Visitor Link" })).toBeVisible();
  await expect(page.getByTestId("qr-placeholder-url")).toContainText("door.local/visitor?token=");
});

async function installMockDoorApi(page: Page) {
  let sessionState = "IDLE";
  let latestRecording: Recording | null = null;
  let inbox: Recording[] = [];

  const snapshot = () => ({
    session: { state: sessionState, session_id: "018f6a61-0000-7000-8000-000000000402" },
    config: { max_recording_s: 5 },
  });
  const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

  // Everything else the kiosk and /admin poll (guestbook, polls, visiond people, …) is
  // refused as if door-api were down, so the test is hermetic even on a machine with a
  // real door-api on :8080. The page must survive that — it does on the Pi whenever a
  // sibling service is restarting. Matched on origin (anything that is not the dev
  // server): a `**/admin/**` glob also swallows Vite's own module scripts and the page
  // never renders. Playwright tries routes newest-first, so the specific handlers
  // registered below win over this one.
  const devOrigin = new URL(test.info().project.use.baseURL ?? "http://127.0.0.1:5173").origin;
  await page.route(
    (url) => url.origin !== devOrigin,
    (route) => route.abort("connectionrefused")
  );

  await page.route("**/session", (route) => route.fulfill(json(snapshot())));

  await page.route("**/doorpad/ring", (route) => {
    sessionState = "VISITOR_MODE";
    return route.fulfill(json({ accepted: true, effect: { status: "unavailable" }, ...snapshot() }));
  });

  await page.route("**/doorpad/video-message/offer", (route) => {
    sessionState = "VIDEO_MESSAGE_OFFERED";
    return route.fulfill(json({ accepted: true, effect: { status: "unavailable" }, ...snapshot() }));
  });

  await page.route("**/doorpad/video-message/start", (route) => {
    sessionState = "VIDEO_MESSAGE_RECORDING";
    latestRecording = null;
    return route.fulfill(json({ accepted: true, ...snapshot() }));
  });

  await page.route("**/doorpad/video-message/stop", (route) => {
    sessionState = "VIDEO_MESSAGE_REVIEW";
    latestRecording = {
      recording_id: "018f6a61-0000-7000-8000-000000000999",
      path: "recordings/mock/video_message.mp4",
      consent_context: "visitor_initiated",
      thumbnail_path: "thumbnails/mock.jpg",
      playback_url: "data:video/mp4;base64,",
    };
    return route.fulfill(json({ accepted: true, ...snapshot() }));
  });

  await page.route("**/doorpad/video-message/latest", (route) =>
    route.fulfill(json({ recording: latestRecording }))
  );

  await page.route("**/doorpad/video-message/save", (route) => {
    sessionState = "VIDEO_MESSAGE_SAVED";
    if (latestRecording) inbox = [latestRecording];
    return route.fulfill(json({ accepted: true, ...snapshot() }));
  });

  await page.route("**/doorpad/video-message/discard", (route) => {
    sessionState = "IDLE";
    latestRecording = null;
    inbox = [];
    return route.fulfill(json({ accepted: true, ...snapshot() }));
  });

  await page.route("**/visitor-token", (route) =>
    route.fulfill(
      json({
        token: "mock-token",
        url: "http://door.local/visitor?token=mock-token",
        expires_at: 1_800_000_000,
      })
    )
  );

  await page.route("**/admin/media-inbox", (route) => route.fulfill(json({ recordings: inbox })));
}
