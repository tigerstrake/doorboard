// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import path from "node:path";

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

import VisitorFlow, { outcomeMessage } from "@/app/v/[token]/VisitorFlow";

/**
 * The public visitor page (ADR-0017).
 *
 * Two things here are worth a test even though they look like plumbing.
 *
 * The first is the write receipt. Visitor writes are eventually consistent: the
 * relay queues them and door-api folds the outcome into a later snapshot. A bug
 * that reached production had door-api republish an empty `outcomes` list on every
 * push, so the outcome the phone was waiting for was overwritten seconds after it
 * arrived and the page said "Sending to the door…" forever. That was found by
 * hand on a real phone; the test below is the component half of the fix.
 *
 * The second is the attribution notice (ADR-0018 E-23). If the door recognises
 * whoever is standing there, their name is attached to what they leave, and they
 * must be told at the point of writing — not discover it from a stats page later.
 * A regression there is a privacy failure, not a copy problem, so it is asserted
 * in both directions.
 */

const TOKEN = "vis_abcdefghijklmnopqrstuv.c2Vzc2lvbi1zZWNyZXQ";

interface Outcome {
  action_id: string;
  kind: string;
  status: string;
  reason: string | null;
  entry_id: string | null;
}

interface SnapshotShape {
  session_id: string;
  state: string;
  expires_at: string;
  attributed?: boolean;
  poll: { poll_id: string; question: string; options: { option_id: string; label: string }[] } | null;
  poll_results: { option_id: string; votes: number }[] | null;
  outcomes: Outcome[];
  pushed_at: string;
}

function snapshot(overrides: Partial<SnapshotShape> = {}): SnapshotShape {
  return {
    session_id: "0123456789abcdef0123456789abcdef",
    state: "RINGING",
    expires_at: new Date(Date.now() + 300_000).toISOString(),
    attributed: false,
    poll: null,
    poll_results: null,
    outcomes: [],
    pushed_at: new Date().toISOString(),
    ...overrides,
  };
}

/**
 * Stub the relay's read and action routes.
 *
 * `live.snapshot` is mutable so a test can change what the next poll returns —
 * which is how the door actually behaves, and the only way to exercise the
 * eventually-consistent receipt path.
 */
function mockVisitorApi(options: {
  snapshot?: SnapshotShape;
  readStatus?: number;
  actionStatus?: number;
  actionBody?: unknown;
} = {}) {
  const live = {
    snapshot: options.snapshot ?? snapshot(),
    readStatus: options.readStatus ?? 200,
  };
  const posted: Array<Record<string, unknown>> = [];

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/action")) {
        const status = options.actionStatus ?? 202;
        posted.push(JSON.parse(String(init?.body ?? "{}")));
        return {
          ok: status < 400,
          status,
          json: async () => options.actionBody ?? { action_id: `act_${posted.length}` },
        } as Response;
      }
      return {
        ok: live.readStatus < 400,
        status: live.readStatus,
        json: async () => live.snapshot,
      } as Response;
    }),
  );

  return { live, posted };
}

/**
 * Advance the clock and let React settle.
 *
 * Timers are faked throughout because the page's whole write model is "ask now,
 * find out on a later poll", and a suite that waited 2s of wall clock per poll
 * would be slow enough that nobody runs it. `waitFor` is deliberately unused:
 * under fake timers it looks for a `jest` global to drive them, which does not
 * exist here, so it hangs until the test times out. Advancing explicitly is also
 * more honest about what the page is waiting for.
 */
async function advance(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

/**
 * Let the page's poll fire once and settle.
 *
 * Advances past the IDLE interval (5s), not the in-flight one (2s): since
 * ADR-0038 the page only polls every 2s while an action is awaiting settlement,
 * and idles slower the rest of the time. Advancing 2.1s no longer guarantees a
 * poll, which is what this helper is for.
 */
async function nextPoll() {
  await advance(5100);
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("access", () => {
  it("shows a mistyped link as not valid", async () => {
    mockVisitorApi({ readStatus: 404 });
    render(<VisitorFlow token={TOKEN} />);
    await advance();
    expect(screen.getByText("Link not valid")).toBeTruthy();
  });

  it("distinguishes a session that ended from a link that never worked", async () => {
    const { live } = mockVisitorApi();
    render(<VisitorFlow token={TOKEN} />);
    await advance();
    expect(screen.getByText("At the door")).toBeTruthy();

    // The door session finishes: the relay starts answering 404.
    live.readStatus = 404;
    await nextPoll();

    // Not "Link not valid" — they scanned correctly and it worked a moment ago.
    expect(screen.getByText("Session ended")).toBeTruthy();
    expect(screen.getByText(/Ring again to start a new one/)).toBeTruthy();
  });

  it("keeps the last snapshot through a transient failure", async () => {
    const { live } = mockVisitorApi();
    render(<VisitorFlow token={TOKEN} />);
    await advance();
    expect(screen.getByText("At the door")).toBeTruthy();

    // A phone dropping to one bar must not look like a closed session.
    live.readStatus = 502;
    await nextPoll();
    expect(screen.getByText("At the door")).toBeTruthy();
    expect(screen.queryByText("Session ended")).toBeNull();
  });
});

describe("attribution disclosure (E-23)", () => {
  it("discloses a write will be attributed, without naming the person (ADR-0044)", async () => {
    mockVisitorApi({ snapshot: snapshot({ attributed: true }) });
    render(<VisitorFlow token={TOKEN} />);

    await advance();
    expect(screen.getByTestId("attribution-notice")).toBeTruthy();
    const notice = screen.getByTestId("attribution-notice");
    // The relay never receives a name (ADR-0044), so none can appear — a stranger who
    // scans this QR must not learn who the door recognised.
    expect(notice.textContent).toMatch(/door recognises you/);
    // It still has to say what happens, not merely that they were recognised.
    expect(notice.textContent).toMatch(/attached to anything you leave/);
    // And it has to appear before the note box, not after they have written.
    const form = document.querySelector("textarea");
    expect(form).not.toBeNull();
    expect(notice.compareDocumentPosition(form!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("says nothing about identity for an unrecognised visitor", async () => {
    mockVisitorApi({ snapshot: snapshot({ attributed: false }) });
    render(<VisitorFlow token={TOKEN} />);
    await advance();
    expect(screen.getByText("At the door")).toBeTruthy();

    expect(screen.queryByTestId("attribution-notice")).toBeNull();
    expect(screen.getByText(/keeps no account and no persistent identity/)).toBeTruthy();
  });
});

describe("leaving a note", () => {
  it("confirms only once the door has actually applied it", async () => {
    // The production bug: the outcome arrived and was then overwritten by a push
    // carrying outcomes=[], so this screen never left "Sending…".
    const { live, posted } = mockVisitorApi();
    render(<VisitorFlow token={TOKEN} />);
    await advance();
    expect(screen.getByText("At the door")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Your message"), {
      target: { value: "Sorry I missed you" },
    });
    fireEvent.click(screen.getByText("Send note"));

    await advance();
    expect(screen.getByText("Sending to the door…")).toBeTruthy();
    expect(posted).toEqual([{ kind: "note", text: "Sorry I missed you" }]);

    // The door collects it and reports back on a later snapshot.
    live.snapshot = snapshot({
      outcomes: [
        { action_id: "act_1", kind: "note", status: "applied", reason: null, entry_id: "gb_1" },
      ],
    });
    await nextPoll();

    expect(screen.getByText(/Your note was delivered/)).toBeTruthy();
    expect(screen.queryByText("Sending to the door…")).toBeNull();

    // A subsequent push must not un-confirm it.
    live.snapshot = snapshot({ outcomes: [], state: "SESSION_END" });
    await nextPoll();
    expect(screen.getByText(/Your note was delivered/)).toBeTruthy();
  });

  it("offers deletion only once there is something to delete", async () => {
    const { live, posted } = mockVisitorApi();
    render(<VisitorFlow token={TOKEN} />);
    await advance();
    expect(screen.getByText("At the door")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Your message"), { target: { value: "hello" } });
    fireEvent.click(screen.getByText("Send note"));
    await advance();
    expect(screen.getByText("Sending to the door…")).toBeTruthy();
    // Nothing to delete while it is still in flight.
    expect(screen.queryByText(/please delete it/)).toBeNull();

    live.snapshot = snapshot({
      outcomes: [
        { action_id: "act_1", kind: "note", status: "applied", reason: null, entry_id: "gb_7" },
      ],
    });
    await nextPoll();

    fireEvent.click(screen.getByText("Actually, please delete it"));
    await advance();
    expect(posted).toHaveLength(2);
    expect(posted[1]).toEqual({
      kind: "deletion_request",
      target_kind: "guestbook",
      target_id: "gb_7",
    });
  });

  it("refuses to send an empty note without troubling the door", async () => {
    const { posted } = mockVisitorApi();
    render(<VisitorFlow token={TOKEN} />);
    await advance();
    expect(screen.getByText("At the door")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Your message"), { target: { value: "   " } });
    fireEvent.click(screen.getByText("Send note"));

    await advance();
    expect(screen.getByText("Please write something first.")).toBeTruthy();
    expect(posted).toHaveLength(0);
  });

  it("closes writing when the session has ended", async () => {
    mockVisitorApi({ snapshot: snapshot({ state: "SESSION_END" }) });
    render(<VisitorFlow token={TOKEN} />);
    await advance();
    expect(screen.getByText("Session ended.")).toBeTruthy();

    expect(document.querySelector("textarea")).toBeNull();
    expect(screen.queryByText("Send note")).toBeNull();
  });

  it("says the door is unreachable rather than blaming the message", async () => {
    mockVisitorApi({ actionStatus: 503, actionBody: {} });
    render(<VisitorFlow token={TOKEN} />);
    await advance();
    expect(screen.getByText("At the door")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Your message"), { target: { value: "hello" } });
    fireEvent.click(screen.getByText("Send note"));

    await advance();
    expect(screen.getByText(/door service is not reachable/)).toBeTruthy();
    // Their text survives the failure, so they can retry without retyping.
    expect((screen.getByLabelText("Your message") as HTMLTextAreaElement).value).toBe("hello");
  });

  it("reports a rejection with the door's own reason", async () => {
    const { live } = mockVisitorApi();
    render(<VisitorFlow token={TOKEN} />);
    await advance();
    expect(screen.getByText("At the door")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Your message"), { target: { value: "hello" } });
    fireEvent.click(screen.getByText("Send note"));
    await advance();
    expect(screen.getByText("Sending to the door…")).toBeTruthy();

    live.snapshot = snapshot({
      outcomes: [
        {
          action_id: "act_1",
          kind: "note",
          status: "rejected",
          reason: "rejected_content",
          entry_id: null,
        },
      ],
    });
    await nextPoll();

    expect(screen.getByText(/declined that message/)).toBeTruthy();
  });
});

describe("the poll", () => {
  const POLL = {
    poll_id: "pol_1",
    question: "Pizza or tacos?",
    options: [
      { option_id: "opt_a", label: "Pizza" },
      { option_id: "opt_b", label: "Tacos" },
    ],
  };

  it("records a vote and then shows the shares", async () => {
    const { live, posted } = mockVisitorApi({ snapshot: snapshot({ poll: POLL }) });
    render(<VisitorFlow token={TOKEN} />);
    await advance();
    expect(screen.getByText("Pizza or tacos?")).toBeTruthy();

    // Nothing to vote for until an option is chosen.
    expect((screen.getByText("Vote") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("Tacos"));
    expect((screen.getByText("Vote") as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(screen.getByText("Vote"));
    await advance();
    expect(posted).toHaveLength(1);
    expect(posted[0]).toEqual({ kind: "vote", poll_id: "pol_1", option_id: "opt_b" });

    live.snapshot = snapshot({
      poll: POLL,
      poll_results: [
        { option_id: "opt_a", votes: 1 },
        { option_id: "opt_b", votes: 3 },
      ],
      outcomes: [
        { action_id: "act_1", kind: "vote", status: "applied", reason: null, entry_id: null },
      ],
    });
    await nextPoll();

    expect(screen.getByText("75%")).toBeTruthy();
    expect(screen.getByText("25%")).toBeTruthy();
    // Voting twice is not on offer.
    expect(screen.queryByText("Vote")).toBeNull();
  });

  it("explains a closed poll instead of losing the vote silently", async () => {
    const { live } = mockVisitorApi({ snapshot: snapshot({ poll: POLL }) });
    render(<VisitorFlow token={TOKEN} />);
    await advance();
    expect(screen.getByText("Pizza or tacos?")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Pizza"));
    fireEvent.click(screen.getByText("Vote"));
    await advance();
    expect(screen.getByText("Recording…")).toBeTruthy();

    live.snapshot = snapshot({
      poll: POLL,
      outcomes: [
        {
          action_id: "act_1",
          kind: "vote",
          status: "rejected",
          reason: "poll_closed",
          entry_id: null,
        },
      ],
    });
    await nextPoll();

    expect(screen.getByText(/poll closed before your vote arrived/)).toBeTruthy();
    // Retrying cannot succeed, so it is not offered at all: being invited to try
    // again and refused identically is worse than being told why.
    expect(screen.queryByText("Try again")).toBeNull();
    expect(screen.queryByText("Vote")).toBeNull();
  });

  it("offers another attempt after a refusal that might not repeat", async () => {
    const { live, posted } = mockVisitorApi({ snapshot: snapshot({ poll: POLL }) });
    render(<VisitorFlow token={TOKEN} />);
    await advance();
    expect(screen.getByText("Pizza or tacos?")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Pizza"));
    fireEvent.click(screen.getByText("Vote"));
    await advance();

    live.snapshot = snapshot({
      poll: POLL,
      outcomes: [
        {
          action_id: "act_1",
          kind: "vote",
          status: "rejected",
          reason: "rate_limited",
          entry_id: null,
        },
      ],
    });
    await nextPoll();

    expect(screen.getByText(/rate-limiting notes right now/)).toBeTruthy();
    const retry = screen.getByText("Try again") as HTMLButtonElement;
    expect(retry.disabled).toBe(false);

    fireEvent.click(retry);
    await advance();
    expect(posted).toHaveLength(2);
    // Back to in-flight, not still showing the old refusal.
    expect(screen.getByText("Recording…")).toBeTruthy();
  });
});

describe("every rejection reason lands somewhere useful", () => {
  it("names every reason door-api can return", () => {
    const source = readFileSync(
      path.join(__dirname, "..", "..", "..", "apps", "door-api", "src", "door_api", "app.py"),
      "utf8",
    );
    const fn = /def _visitor_reject_reason[\s\S]*?\n\n\n/.exec(source)?.[0];
    expect(fn).toBeTruthy();

    // Every lowercase string literal in that function is a reason: the dict keys
    // are exception class names (CamelCase), and the default sits in a `.get()`
    // call rather than the mapping, which an over-precise regex would miss.
    const reasons = [...fn!.matchAll(/"([a-z][a-z_]*)"/g)].map((match) => match[1]!);
    // Guard the scraper: an empty set would make this pass by covering nothing.
    expect(reasons).toContain("door_error");
    expect(reasons).toContain("rate_limited");

    // `empty_action` is set at the call site rather than in the map.
    for (const reason of [...new Set(reasons), "empty_action", "session_mismatch"]) {
      expect(outcomeMessage(reason)).not.toContain(`(${reason})`);
    }
  });

  it("echoes an unrecognised reason rather than swallowing it", () => {
    expect(outcomeMessage("something_new")).toContain("something_new");
    expect(outcomeMessage(null)).toContain("no reason given");
  });
});

/**
 * ADR-0038. Every poll here is a metered serverless invocation.
 *
 * This page used to poll every 2s with no stop condition, so one forgotten tab
 * cost ~43,000 requests a day — more than the door itself did, and enough on its
 * own to consume a 1,000,000/month free tier. An EXPIRED link kept polling too,
 * because nothing checked.
 */
describe("polling cost", () => {
  function readCount(): number {
    const fetchMock = globalThis.fetch as unknown as { mock: { calls: unknown[][] } };
    return fetchMock.mock.calls.filter(([url]) => !String(url).endsWith("/action")).length;
  }

  it("stops entirely once the link is dead", async () => {
    // A 404 from the start: the link never worked and never will.
    mockVisitorApi({ readStatus: 404 });
    render(<VisitorFlow token={TOKEN} />);
    await advance();
    const after1 = readCount();

    await advance(60_000);
    expect(readCount()).toBe(after1);
  });

  it("stops after a session ends, rather than polling a dead session forever", async () => {
    const { live } = mockVisitorApi();
    render(<VisitorFlow token={TOKEN} />);
    await advance();
    live.readStatus = 404;
    await nextPoll();
    const settledCount = readCount();

    await advance(60_000);
    expect(readCount()).toBe(settledCount);
  });

  it("pauses while the tab is hidden", async () => {
    mockVisitorApi();
    render(<VisitorFlow token={TOKEN} />);
    await advance();
    const before = readCount();

    // Override on the INSTANCE, and delete the instance property afterwards so the
    // prototype's getter is visible again. Restoring Document.prototype instead
    // leaves this override in place and every later test's poll pauses forever.
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "hidden",
    });
    try {
      await advance(60_000);
      // A hidden tab has nobody watching it; browsers throttle background timers
      // but do not stop them, so this has to be explicit.
      expect(readCount()).toBe(before);
    } finally {
      Reflect.deleteProperty(document, "visibilityState");
    }
  });

  it("gives up after a long quiet spell with nothing pending", async () => {
    mockVisitorApi();
    render(<VisitorFlow token={TOKEN} />);
    await advance();

    // Past the 10-minute idle stop.
    await advance(11 * 60 * 1000);
    const afterStop = readCount();
    await advance(60_000);
    expect(readCount()).toBe(afterStop);
  });

  it("polls faster while a note is awaiting the door than when idle", async () => {
    mockVisitorApi();
    render(<VisitorFlow token={TOKEN} />);
    await advance();
    expect(screen.getByText("At the door")).toBeTruthy();

    // Idle: one poll every 5s, so under 4s there should be at most one.
    const idleStart = readCount();
    await advance(4_000);
    const idlePolls = readCount() - idleStart;

    // Submit a note: now the visitor is actually waiting on the door, and the
    // page should tighten to 2s until the outcome settles.
    fireEvent.change(screen.getByLabelText("Your message"), {
      target: { value: "Sorry I missed you" },
    });
    fireEvent.click(screen.getByText("Send note"));
    await advance();

    const pendingStart = readCount();
    await advance(4_000);
    const pendingPolls = readCount() - pendingStart;

    expect(pendingPolls).toBeGreaterThan(idlePolls);
  });
});
