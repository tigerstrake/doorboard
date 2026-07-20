// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from "vitest";
import { safeRandomUUID } from "./uuid";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const UUID_V4_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("safeRandomUUID", () => {
  it("returns a well-formed UUID (native crypto.randomUUID path)", () => {
    expect(safeRandomUUID()).toMatch(UUID_RE);
  });

  it("falls back to a v4 UUID in an insecure context where randomUUID is absent", () => {
    // Regression for the blank visitor page: the phone loads the UI over plain
    // HTTP on a LAN IP, which is not a secure context, so crypto.randomUUID is
    // undefined. getRandomValues is still available there.
    vi.stubGlobal("crypto", {
      getRandomValues: (arr: Uint8Array) => {
        for (let i = 0; i < arr.length; i += 1) arr[i] = i;
        return arr;
      },
    });
    expect(safeRandomUUID()).toMatch(UUID_V4_RE);
  });

  it("still returns a UUID when the Web Crypto API is entirely absent", () => {
    vi.stubGlobal("crypto", undefined);
    expect(safeRandomUUID()).toMatch(UUID_RE);
  });
});
