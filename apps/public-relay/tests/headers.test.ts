/**
 * `public/_headers` is copied verbatim into the static export and applied by Cloudflare Pages
 * (ADR-0043); there is no runtime to test it through, so this pins the file. The CSP is the
 * cheap complement to ADR-0016 §9's accepted residual risk: served JavaScript that reads
 * plaintext before sealing must still have nowhere to send it but this origin.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const HEADERS = readFileSync(path.join(__dirname, "..", "public", "_headers"), "utf8");

/** The directives applied to every path, as `name: value`. */
function globalHeaders(): Record<string, string> {
  const block = HEADERS.split(/^\/\*\s*$/m)[1]?.split(/^\S/m)[0] ?? "";
  return Object.fromEntries(
    block
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0 && !line.startsWith("#"))
      .map((line) => {
        const colon = line.indexOf(":");
        return [line.slice(0, colon).trim(), line.slice(colon + 1).trim()];
      }),
  );
}

describe("public/_headers", () => {
  it("sets a CSP whose only network sink is this origin", () => {
    const csp = globalHeaders()["Content-Security-Policy"];
    expect(csp).toBeDefined();
    const directives = Object.fromEntries(
      csp!.split(";").map((d) => {
        const [name, ...values] = d.trim().split(/\s+/);
        return [name, values.join(" ")];
      }),
    );
    expect(directives["default-src"]).toBe("'self'");
    expect(directives["connect-src"]).toBe("'self'");
    expect(directives["form-action"]).toBe("'none'");
    expect(directives["base-uri"]).toBe("'none'");
    expect(directives["frame-ancestors"]).toBe("'none'");
    // Local camera previews are blob:/data: URLs; nothing remote.
    expect(directives["img-src"]).toBe("'self' blob: data:");
    for (const value of Object.values(directives)) {
      expect(value).not.toMatch(/https?:|\*/);
    }
  });

  it("sets HSTS for a year", () => {
    expect(globalHeaders()["Strict-Transport-Security"]).toMatch(/max-age=31536000/);
  });

  it("keeps the existing hardening headers", () => {
    const headers = globalHeaders();
    expect(headers["X-Frame-Options"]).toBe("DENY");
    expect(headers["X-Content-Type-Options"]).toBe("nosniff");
    expect(headers["Referrer-Policy"]).toBe("no-referrer");
    expect(headers["Permissions-Policy"]).toContain("camera=(self)");
  });

  it("never lets a per-person surface be cached", () => {
    for (const prefix of ["/api/*", "/e/*", "/v/*"]) {
      const section = HEADERS.split(prefix)[1]?.split(/^\S/m)[0] ?? "";
      expect(section, prefix).toContain("Cache-Control: no-store");
    }
  });
});
