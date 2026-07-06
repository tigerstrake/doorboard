import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

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
