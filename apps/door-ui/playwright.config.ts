import { defineConfig, devices } from "@playwright/test";

const WALLBOARD_PORT = 4173;
const WALLBOARD_URL = `http://127.0.0.1:${WALLBOARD_PORT}`;
const VISITOR_PORT = 4576;
const VISITOR_URL = `http://localhost:${VISITOR_PORT}`;

// Two independent Playwright suites share this package:
//   - tests/ (T-202): live WebRTC preview, spawns a real door-media
//     process on a fixed port — must run with a single worker so
//     parallel runs don't race for that port.
//   - e2e/ (T-404): wallboard visitor-mode takeover, pure mock event bus,
//     no external services, screenshot baselines.
// Each project pins its own testDir so `playwright test` runs both without
// a shared top-level testDir forcing one directory's naming convention on
// the other.
export default defineConfig({
  timeout: 30_000,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  expect: {
    timeout: 5_000,
    // Backdrop-filter blur (used throughout the glass-tile UI) rasterizes with a
    // small amount of GPU-driven anti-aliasing jitter between paints on this
    // renderer — tolerate a couple hundred stray pixels so baselines aren't flaky
    // while still catching real layout/content/color regressions.
    toHaveScreenshot: { maxDiffPixelRatio: 0.02 },
  },
  webServer: [
    {
      command: `pnpm dev --host 127.0.0.1 --port ${WALLBOARD_PORT}`,
      url: `${WALLBOARD_URL}/live-view-demo`,
      reuseExistingServer: !process.env.CI,
      env: {
        VITE_DOOR_MEDIA_TARGET: "http://127.0.0.1:18082",
      },
    },
    {
      command: `pnpm exec vite --port ${VISITOR_PORT} --strictPort`,
      url: VISITOR_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      env: {
        VITE_AMBIENT_MOCK: "true",
        VITE_AIRCRAFT_ALERT_DISTANCE_KM: "3",
      },
    },
  ],
  projects: [
    {
      name: "wallboard",
      testDir: "./tests",
      use: { ...devices["Desktop Chrome"], baseURL: WALLBOARD_URL, viewport: { width: 1920, height: 1080 } },
    },
    {
      name: "doorpad",
      testDir: "./tests",
      use: { ...devices["Desktop Chrome"], baseURL: WALLBOARD_URL, viewport: { width: 1024, height: 600 } },
    },
    {
      name: "phone-lan",
      testDir: "./tests",
      use: { ...devices["Pixel 7"], baseURL: WALLBOARD_URL },
    },
    {
      name: "1080p-landscape",
      testDir: "./e2e",
      use: { ...devices["Desktop Chrome"], baseURL: VISITOR_URL, viewport: { width: 1920, height: 1080 } },
    },
    {
      name: "1080p-portrait",
      testDir: "./e2e",
      use: { ...devices["Desktop Chrome"], baseURL: VISITOR_URL, viewport: { width: 1080, height: 1920 } },
    },
  ],
});
