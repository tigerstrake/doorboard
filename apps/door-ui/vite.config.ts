import react from "@vitejs/plugin-react";
// vitest/config re-exports vite's defineConfig with its ambient `test` key
// augmentation applied — importing from plain "vite" fails typecheck as
// soon as a `test` block is present in this config.
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    // Vite's dev server rejects any Host header it was not told about, so opening
    // /admin from a laptop at http://door-pi.local:5173 returned a bare
    // "Blocked request. This host is not allowed." — while the same page over the
    // IP worked, which makes it look like a name-resolution problem rather than a
    // deliberate check. The door serves the kiosk through this dev server (see
    // deploy/pi-door/install-home-layout), so the hostnames it is actually reached
    // by have to be listed.
    //
    // Named rather than `true`: allowing every host would also allow DNS-rebinding
    // against a dev server that is bound to 0.0.0.0 on the house LAN.
    allowedHosts: [
      "door-pi.local",
      "door.local",
      "localhost",
      "127.0.0.1",
    ],
    proxy: {
      "/door-media": {
        target: process.env.VITE_DOOR_MEDIA_TARGET ?? "http://127.0.0.1:8082",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/door-media/, ""),
      },
    },
  },
  test: {
    // vitest's default include glob matches *.spec.ts anywhere in the
    // package, which would otherwise pick up the Playwright specs under
    // e2e/ and tests/ (run separately via `pnpm test:e2e`) and crash —
    // Playwright's test() API isn't compatible with vitest's runner.
    exclude: ["e2e/**", "tests/**", "node_modules/**"],
  },
});
