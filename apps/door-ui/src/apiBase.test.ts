import { describe, it, expect } from "vitest";
import { resolveApiBase, wsUrlFromApiBase } from "./apiBase";

describe("resolveApiBase", () => {
  it("uses an explicit VITE_DOOR_API_BASE_URL verbatim", () => {
    expect(
      resolveApiBase("https://api.example.test:9000", {
        protocol: "http:",
        hostname: "door-pi.local",
      })
    ).toBe("https://api.example.test:9000");
  });

  it("derives from the page host when unset (kiosk at localhost → :8080)", () => {
    expect(
      resolveApiBase(undefined, { protocol: "http:", hostname: "localhost" })
    ).toBe("http://localhost:8080");
  });

  it("derives from the page host when set to the literal \"auto\" (LAN laptop)", () => {
    expect(
      resolveApiBase("auto", { protocol: "http:", hostname: "door-pi.local" })
    ).toBe("http://door-pi.local:8080");
  });

  it("keeps the page protocol when deriving (https → https)", () => {
    expect(
      resolveApiBase(undefined, { protocol: "https:", hostname: "door-pi.local" })
    ).toBe("https://door-pi.local:8080");
  });

  it("falls back to the historical default with no window (SSR/tests)", () => {
    expect(resolveApiBase(undefined, undefined)).toBe("http://127.0.0.1:8000");
  });
});

describe("wsUrlFromApiBase", () => {
  it("maps an http base to a ws /ws endpoint on the same host:port", () => {
    expect(wsUrlFromApiBase("http://door-pi.local:8080")).toBe(
      "ws://door-pi.local:8080/ws"
    );
  });

  it("maps an https base to a wss /ws endpoint", () => {
    expect(wsUrlFromApiBase("https://door-pi.local:8080")).toBe(
      "wss://door-pi.local:8080/ws"
    );
  });
});
