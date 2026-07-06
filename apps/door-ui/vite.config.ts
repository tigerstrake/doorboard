import react from "@vitejs/plugin-react";
// vitest/config re-exports vite's defineConfig with its ambient `test` key
// augmentation applied — importing from plain "vite" fails typecheck as
// soon as a `test` block is present in this config.
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    // vitest's default include glob matches *.spec.ts anywhere in the
    // package, which would otherwise pick up the Playwright specs under
    // e2e/ (run separately via `pnpm test:e2e`) and crash — Playwright's
    // test() API isn't compatible with vitest's runner.
    exclude: ["e2e/**", "node_modules/**"],
  },
});
