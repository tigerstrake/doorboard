import { describe, it, expect } from "vitest";
import { uuidv7 } from "./uuid";

describe("uuidv7", () => {
  it("should generate a valid UUIDv7 format", () => {
    const id = uuidv7();
    
    // Check format: 8-4-4-4-12 hex chars
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  });

  it("should embed the current timestamp correctly", () => {
    const before = Date.now();
    const id = uuidv7();
    const after = Date.now();

    // Extract the timestamp part (first 48 bits, 12 hex chars)
    const tsHex = id.split("-")[0] + id.split("-")[1];
    const embeddedTime = parseInt(tsHex, 16);

    expect(embeddedTime).toBeGreaterThanOrEqual(before);
    expect(embeddedTime).toBeLessThanOrEqual(after);
  });

  it("should generate unique values", () => {
    const ids = new Set<string>();
    for (let i = 0; i < 1000; i++) {
      const id = uuidv7();
      expect(ids.has(id)).toBe(false);
      ids.add(id);
    }
    expect(ids.size).toBe(1000);
  });
});
