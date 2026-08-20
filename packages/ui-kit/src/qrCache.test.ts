import { beforeEach, describe, expect, it, vi } from "vitest";
import { cachedQr, encodeQr, warmQrEncoder } from "./qrCache";

/**
 * The doorpad's QR "took ages to prepare". Measured on the door, the backend was not the
 * problem — minting an enrollment invite is 16-44 ms — so these pin the two client-side
 * costs that were: paying for the encoder on first use, and re-encoding a code that had
 * already been generated.
 */

const VISITOR_URL = "https://door.example/v/abc123";

describe("qr cache", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("returns nothing for a code it has not seen", () => {
    expect(cachedQr("https://door.example/never-encoded", { size: 320 })).toBeNull();
  });

  it("encodes a code and serves it from cache afterwards", async () => {
    const first = await encodeQr(VISITOR_URL, { size: 320 });

    expect(first.startsWith("data:image/")).toBe(true);
    // The point: the second render paints on the first frame rather than awaiting.
    expect(cachedQr(VISITOR_URL, { size: 320 })).toBe(first);
    expect(await encodeQr(VISITOR_URL, { size: 320 })).toBe(first);
  });

  it("treats a different size as a different code", async () => {
    const small = await encodeQr(VISITOR_URL, { size: 160 });
    const large = await encodeQr(VISITOR_URL, { size: 320 });

    // A cache keyed on the URL alone would hand a room-scale code a thumbnail.
    expect(small).not.toBe(large);
    expect(cachedQr(VISITOR_URL, { size: 160 })).toBe(small);
  });

  it("stays bounded on a door that runs for weeks", async () => {
    for (let i = 0; i < 40; i += 1) {
      await encodeQr(`https://door.example/v/bulk-${i}`, { size: 96 });
    }

    // The oldest entries are evicted; the newest are still there.
    expect(cachedQr("https://door.example/v/bulk-0", { size: 96 })).toBeNull();
    expect(cachedQr("https://door.example/v/bulk-39", { size: 96 })).not.toBeNull();
  });

  it("warms up at most once and never throws", async () => {
    await expect(warmQrEncoder()).resolves.toBeUndefined();
    await expect(warmQrEncoder()).resolves.toBeUndefined();
  });

  it("does not cache a failed encode", async () => {
    // A URL too long for any QR version: the encoder rejects it.
    const impossible = "https://door.example/".concat("x".repeat(8000));

    await expect(encodeQr(impossible, { size: 320 })).rejects.toBeTruthy();
    expect(cachedQr(impossible, { size: 320 })).toBeNull();
  });
});
