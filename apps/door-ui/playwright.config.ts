import { defineConfig, devices } from "@playwright/test";

const PORT = 4576;
const baseURL = `http://localhost:${PORT}`;

// Wallboard/DoorPad e2e coverage (T-404). No hardware, no network — the app runs
// entirely against its local mock event bus (see e2e/helpers.ts). Kept local to
// this package (rather than the shared tests/e2e/ from tests/README.md) so specs
// resolve @playwright/test through this package's own devDependency without a
// new pnpm workspace member.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  // Backdrop-filter blur (used throughout the glass-tile UI) rasterizes with a
  // small amount of GPU-driven anti-aliasing jitter between paints on this
  // renderer — tolerate a couple hundred stray pixels so baselines aren't flaky
  // while still catching real layout/content/color regressions.
  expect: {
    toHaveScreenshot: { maxDiffPixelRatio: 0.02 },
  },
  webServer: {
    command: `pnpm exec vite --port ${PORT} --strictPort`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  projects: [
    {
      name: "1080p-landscape",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1920, height: 1080 } },
    },
    {
      name: "1080p-portrait",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1080, height: 1920 } },
    },
  ],
});
