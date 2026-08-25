import path from "node:path";

import type { NextConfig } from "next";

/**
 * The pnpm workspace root. Turbopack's module resolution has to start here: `next` and
 * `@doorboard/contracts` are hoisted to the monorepo's node_modules, above this app.
 */
const WORKSPACE_ROOT = path.join(__dirname, "..", "..");

/**
 * Relay build configuration (ADR-0016, ADR-0043).
 *
 * `output: "export"` produces a fully static site (Cloudflare Pages): the two flows are
 * client-rendered shells, the API is Cloudflare Pages Functions (`functions/api/**`), and no
 * Next server runtime runs in production. The security/cache headers that `headers()` used to
 * set do not run under a static export, so they live in `public/_headers` instead; the SPA
 * fallback for the dynamic page routes lives in `public/_redirects`. This deployment is still
 * a blind courier — ciphertext and opaque ids only, no decryption key.
 */
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  turbopack: { root: WORKSPACE_ROOT },
  reactStrictMode: true,
  poweredByHeader: false,
};

export default nextConfig;
