import { test, expect } from "@playwright/test";
import { gotoWallboard, publishSessionState } from "./helpers";

// Automated proxy for the 1-hour idle+takeover soak (see e2e/SOAK.md for the real
// bench procedure). Real time runs here (no clock.setFixedTime) so intervals and
// animations behave as they would on a kiosk. Cycle count is small by default for
// CI; scale it up locally with SOAK_CYCLES for a longer confidence run.
test("idle<->visitor-mode cycling does not grow the JS heap unbounded", async ({ page }) => {
  test.setTimeout(0);

  await gotoWallboard(page);
  const sessionId = "ses_soak";
  const cycles = Number(process.env.SOAK_CYCLES ?? 30);

  const readHeap = () =>
    page.evaluate(() => {
      const mem = (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory;
      return mem?.usedJSHeapSize ?? 0;
    });

  const baseline = await readHeap();
  const samples: number[] = [];

  for (let i = 0; i < cycles; i++) {
    await publishSessionState(page, sessionId, "IDLE", "VISITOR_MODE");
    await page.waitForTimeout(50);
    await publishSessionState(page, sessionId, "VISITOR_MODE", "RINGING");
    await page.waitForTimeout(50);
    await publishSessionState(page, sessionId, "RINGING", "SESSION_END");
    await page.waitForTimeout(50);
    await publishSessionState(page, sessionId, "SESSION_END", "IDLE");
    await page.waitForTimeout(50);

    if (i % 5 === 0) samples.push(await readHeap());
  }
  samples.push(await readHeap());

  // No cameras/hardware in this environment doesn't affect the wallboard's own
  // React tree, so any unbounded growth here (leaked listeners, growing arrays)
  // is a real bug regardless of how much of the full kiosk stack is running.
  await expect(page.getByTestId("wallboard-visitor-mode")).toHaveCount(0);

  if (baseline > 0) {
    const growth = (samples[samples.length - 1] - baseline) / baseline;
    // Generous bound — this is a fast smoke proxy, not the real 1h bench gate.
    expect(growth).toBeLessThan(2);
  }
});
