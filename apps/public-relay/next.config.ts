import path from "node:path";

import type { NextConfig } from "next";

/**
 * The pnpm workspace root. Both Turbopack's module resolution and file tracing
 * have to start here: `next` and `@doorboard/contracts` are hoisted to the
 * monorepo's node_modules, above this app.
 */
const WORKSPACE_ROOT = path.join(__dirname, "..", "..");

/**
 * Relay configuration (ADR-0016).
 *
 * This deployment is a blind courier: it holds ciphertext and opaque ids, never a
 * decryption key. Two things here are load-bearing rather than cosmetic:
 *
 * - `outputFileTracingRoot` lets the app build from inside the pnpm monorepo.
 * - The headers below keep the sealing page from being framed or from leaking the
 *   invite URL (which contains the invite secret) through a Referer header.
 */
const nextConfig: NextConfig = {
  outputFileTracingRoot: WORKSPACE_ROOT,
  turbopack: { root: WORKSPACE_ROOT },
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          // The invite secret lives in the URL path, so it must never travel in a
          // Referer to any third party.
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          {
            key: "Permissions-Policy",
            // The page needs the camera; it needs nothing else.
            value: "camera=(self), microphone=(), geolocation=(), payment=()",
          },
        ],
      },
      {
        // Invite pages and API responses must never be cached by a CDN or a
        // shared proxy: they are single-use and carry per-person state.
        source: "/(api|e)/:path*",
        headers: [{ key: "Cache-Control", value: "no-store, max-age=0" }],
      },
    ];
  },
};

export default nextConfig;
