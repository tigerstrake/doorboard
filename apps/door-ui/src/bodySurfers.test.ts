import { describe, expect, it } from "vitest";
import {
  bodySurfersUrls,
  gameMessageFrom,
  siblingLoopbackHost,
  startRefusedMessage,
  wallboardGameMessageFrom,
} from "./bodySurfers";

const kiosk = { protocol: "http:", hostname: "127.0.0.1", port: "5173", origin: "http://127.0.0.1:5173" };

describe("Body Surfers frame URLs (ADR-0049)", () => {
  it("frames the game from the other loopback name so it gets its own process", () => {
    expect(siblingLoopbackHost("127.0.0.1")).toBe("localhost");
    expect(siblingLoopbackHost("localhost")).toBe("127.0.0.1");
    expect(siblingLoopbackHost("door-pi.local")).toBe("door-pi.local");

    const urls = bodySurfersUrls(kiosk);
    const game = new URL(urls.game);
    expect(game.origin).toBe("http://localhost:5173");
    expect(urls.origin).toBe("http://localhost:5173");
    expect(game.pathname).toBe("/games/body-surfers/index.html");
    expect(game.searchParams.get("door")).toBe("1");
    expect(game.searchParams.get("parent")).toBe("http://127.0.0.1:5173");
    expect(game.searchParams.get("media")).toBe("http://127.0.0.1:8082/streams");
  });

  it("puts the leaderboard clear page next to the game, without the camera params", () => {
    const clear = new URL(bodySurfersUrls(kiosk).clear);
    expect(clear.origin).toBe("http://localhost:5173");
    expect(clear.pathname).toBe("/games/body-surfers/clear.html");
    expect(clear.searchParams.get("parent")).toBe("http://127.0.0.1:5173");
    expect(clear.searchParams.has("media")).toBe(false);
  });

  it("honours an override page URL and keeps its query (e.g. ?webcam=1)", () => {
    const urls = bodySurfersUrls(kiosk, {
      override: "http://localhost:5174/index.html?webcam=1",
      mediaUrl: "http://127.0.0.1:9999/streams",
    });
    const game = new URL(urls.game);
    expect(game.origin).toBe("http://localhost:5174");
    expect(game.searchParams.get("webcam")).toBe("1");
    expect(game.searchParams.get("media")).toBe("http://127.0.0.1:9999/streams");
    expect(new URL(urls.clear).pathname).toBe("/clear.html");
  });
});

describe("wallboard.game_changed messages", () => {
  const base = {
    type: "wallboard.game_changed",
    game: "body_surfers",
    reason: "doorpad",
    requestId: "r1",
    requestedAt: 1,
  };

  it("reads start, stop and clear", () => {
    for (const action of ["start", "stop", "clear_leaderboard"] as const) {
      expect(wallboardGameMessageFrom({ ...base, action })?.action).toBe(action);
    }
  });

  it("rejects other types, other games and unknown actions", () => {
    expect(wallboardGameMessageFrom({ ...base, type: "wallboard.focus_changed", action: "start" })).toBeNull();
    expect(wallboardGameMessageFrom({ ...base, game: "tetris", action: "start" })).toBeNull();
    expect(wallboardGameMessageFrom({ ...base, action: "restart" })).toBeNull();
    expect(wallboardGameMessageFrom({ ...base, action: "start", requestId: 7 })).toBeNull();
    expect(wallboardGameMessageFrom(null)).toBeNull();
  });
});

describe("messages from the game frame", () => {
  it("reads ready, exit and leaderboard-cleared on the body-surfers channel only", () => {
    expect(gameMessageFrom({ channel: "body-surfers", type: "ready" })).toEqual({ type: "ready" });
    expect(gameMessageFrom({ channel: "body-surfers", type: "exit", reason: "walked_away" })).toEqual({
      type: "exit",
      reason: "walked_away",
    });
    expect(gameMessageFrom({ channel: "body-surfers", type: "leaderboard-cleared" })).toEqual({
      type: "leaderboard-cleared",
    });
    expect(gameMessageFrom({ channel: "other", type: "ready" })).toBeNull();
    expect(gameMessageFrom({ channel: "body-surfers", type: "navigate" })).toBeNull();
  });

  it("treats an exit with an unknown reason as an error exit", () => {
    expect(gameMessageFrom({ channel: "body-surfers", type: "exit", reason: "??" })).toEqual({
      type: "exit",
      reason: "error",
    });
  });
});

describe("start refusals", () => {
  it("explains door-api's 409s in visitor terms", () => {
    expect(startRefusedMessage("privacy_mode")).toMatch(/camera is off/);
    expect(startRefusedMessage("visitor_session")).toMatch(/at the door/);
    expect(startRefusedMessage(undefined)).toMatch(/didn't answer/);
  });
});
