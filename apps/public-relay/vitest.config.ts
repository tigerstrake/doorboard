import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // The API-route and crypto suites are Node; the page suites need a DOM. Rather
    // than one global environment, each file declares its own via the
    // `@vitest-environment` pragma, with Node as the default.
    environment: "node",
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    // Applies to every file; the DOM-specific parts no-op under Node.
    setupFiles: ["./tests/setup.dom.ts"],
  },
  resolve: {
    alias: { "@": new URL("./", import.meta.url).pathname },
  },
});
