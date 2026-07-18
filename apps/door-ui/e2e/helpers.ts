import type { Page } from "@playwright/test";

export async function gotoWallboard(page: Page) {
  await page.goto("/wallboard");
}

// Masks content that legitimately differs run-to-run and isn't what these
// baselines are meant to catch regressions in:
//  - the dev-only sim control panel/toggle (T-004, never ships on kiosk hardware;
//    its box-shadow also rasterizes with a few non-deterministic pixels)
//  - the QR card, whose URL embeds a per-session id (masking the whole card, not
//    just the url text span, avoids sub-pixel bounding-box jitter at the text edge)
export function screenshotMasks(page: Page) {
  return [
    page.locator(".sim-panel-overlay"),
    page.locator(".sim-panel-toggle-btn"),
    page.getByTestId("qr-placeholder"),
  ];
}

// Mirrors packages/event-client's BroadcastChannel("doorboard-events") transport.
// Posting a contract-shaped event here exercises the exact same client code path
// a real WebSocket delta would (DoorboardEventClient.notifyListeners) without
// needing a live door-api/simulator backend.
async function publish(page: Page, event: Record<string, unknown>) {
  await page.evaluate((evt) => {
    new BroadcastChannel("doorboard-events").postMessage(evt);
  }, event);
}

export async function publishAircraftSummary(page: Page, distanceKm: number) {
  await publish(page, {
    event_id: nextId("evt"),
    type: "ambient.aircraft_summary",
    source: "e2e",
    occurred_at: new Date(0).toISOString(),
    monotonic_ms: 0,
    door_id: "primary",
    trace_id: nextId("trace"),
    payload: {
      as_of: new Date(0).toISOString(),
      nearby: [{ callsign: "TEST123", altitude_ft: 4200, distance_km: distanceKm, heading: 87 }],
    },
  });
}

export async function publishBirdSummary(page: Page, totalDetections: number) {
  await publish(page, {
    event_id: nextId("evt"),
    type: "ambient.bird_summary",
    source: "e2e",
    occurred_at: new Date(0).toISOString(),
    monotonic_ms: 0,
    door_id: "primary",
    trace_id: nextId("trace"),
    payload: {
      window: "today",
      total_detections: totalDetections,
      top_species: [{ name: "Pacific Swift", count: totalDetections, confidence_avg: 0.91 }],
    },
  });
}

let counter = 0;
function nextId(prefix: string) {
  counter += 1;
  return `${prefix}-${counter}`;
}

export async function publishSessionState(
  page: Page,
  sessionId: string,
  fromState: string,
  toState: string,
) {
  await publish(page, {
    event_id: nextId("evt"),
    type: "session.state_changed",
    source: "e2e",
    occurred_at: new Date(0).toISOString(),
    monotonic_ms: 0,
    door_id: "primary",
    trace_id: nextId("trace"),
    payload: {
      session_id: sessionId,
      from_state: fromState,
      to_state: toState,
      trigger: "e2e",
    },
  });
}

export async function publishIdentityStable(
  page: Page,
  profileId: string,
  displayName: string,
) {
  await publish(page, {
    event_id: nextId("evt"),
    type: "vision.identity_stable",
    source: "e2e",
    occurred_at: new Date(0).toISOString(),
    monotonic_ms: 0,
    door_id: "primary",
    trace_id: nextId("trace"),
    payload: {
      person_id: `prs_${profileId}`,
      display_name: displayName,
      confidence: 0.97,
      expires_at: new Date(Date.now() + 60_000).toISOString(),
      expires_at_monotonic_ms: 60_000,
      profile_id: profileId,
    },
  });
}
