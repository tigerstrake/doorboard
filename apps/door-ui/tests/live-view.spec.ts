import { expect, test, type Page } from "@playwright/test";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const TEST_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(TEST_DIR, "../../..");
const DOOR_MEDIA_URL = "http://127.0.0.1:18082";

let mediaProcess: ChildProcessWithoutNullStreams | null = null;
let mediaRoot: string | null = null;

test.beforeEach(async () => {
  await startDoorMedia();
});

test.afterEach(async () => {
  await stopDoorMedia();
  if (mediaRoot) {
    rmSync(mediaRoot, { recursive: true, force: true });
    mediaRoot = null;
  }
});

test("live preview connects, reports restart honestly, and recovers", async ({ page }) => {
  await page.goto("/live-view-demo");
  await expectState(page, "Connected");

  const initialStats = await activePeerConnections(page);
  expect(initialStats).toBe(0);

  await stopDoorMedia();
  await expectState(page, "Video unavailable");
  expect(await activePeerConnections(page)).toBe(0);

  await startDoorMedia();
  await expectState(page, "Connected");
  expect(await activePeerConnections(page)).toBe(0);
});

async function expectState(page: Page, text: string): Promise<void> {
  await expect(page.getByTestId("live-video-state")).toHaveText(text, { timeout: 5_000 });
}

async function activePeerConnections(page: Page): Promise<number> {
  return page.getByText("PeerConnections").evaluate((element) => {
    const value = element.parentElement?.querySelector("dd")?.textContent ?? "0";
    return Number.parseInt(value, 10);
  });
}

async function startDoorMedia(): Promise<void> {
  if (mediaProcess) {
    return;
  }
  if (mediaRoot) {
    rmSync(mediaRoot, { recursive: true, force: true });
  }
  mediaRoot = mkdtempSync(path.join(tmpdir(), "doorboard-media-e2e-"));
  mediaProcess = spawn(
    "uv",
    [
      "run",
      "uvicorn",
      "door_media.app:app",
      "--host",
      "127.0.0.1",
      "--port",
      "18082",
      "--log-level",
      "warning",
    ],
    {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        MEDIA_MODE: "mock",
        SSD_DATA_ROOT: mediaRoot,
        DOOR_MEDIA_BIND: "127.0.0.1:18082",
        DOOR_MEDIA_STORAGE_STATUS_INTERVAL_S: "3600",
      },
    },
  );
  mediaProcess.once("exit", () => {
    mediaProcess = null;
  });
  await waitForHealth();
}

async function stopDoorMedia(): Promise<void> {
  const proc = mediaProcess;
  if (!proc) {
    return;
  }
  mediaProcess = null;
  await new Promise<void>((resolve) => {
    const timeout = setTimeout(() => {
      proc.kill("SIGKILL");
      resolve();
    }, 3_000);
    proc.once("exit", () => {
      clearTimeout(timeout);
      resolve();
    });
    proc.kill("SIGTERM");
  });
}

async function waitForHealth(): Promise<void> {
  // The first `uv run` in a clean kiosk/CI environment may spend several
  // seconds warming its package cache before uvicorn begins listening.
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${DOOR_MEDIA_URL}/health`);
      if (response.ok) {
        return;
      }
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 250));
      continue;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("door-media mock server did not become healthy");
}
