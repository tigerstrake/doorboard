import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default [
  {
    // Build output is generated, not authored: linting it produces thousands of
    // meaningless errors and hides the real ones.
    ignores: [
      "**/dist/**",
      "**/node_modules/**",
      "**/.venv/**",
      "**/.next/**",
      // The relay's static export (Cloudflare Pages build) and wrangler's build temp;
      // generated, not authored.
      "**/out/**",
      "**/.wrangler/**",
      "**/next-env.d.ts",
      // Agent worktrees are separate checkouts nested inside the repo; their
      // in-progress files are not part of this tree and must not fail its lint.
      "**/.claude/worktrees/**",
      // Body Surfers: the MediaPipe runtime is copied from node_modules at build time
      // (vendor JS, not authored here), and e2e/, tools/ and scripts/ are dev-only
      // Node and browser harnesses that never ship to the door.
      "apps/body-surfers/public/mediapipe/**",
      "apps/body-surfers/e2e/**",
      "apps/body-surfers/tools/**",
      "apps/body-surfers/scripts/**",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { "allowConstantExport": true }],
    },
  },
  {
    // Next.js App Router files are required to export non-components
    // (`metadata`, `viewport`, `dynamic`, route handlers), so the Vite-oriented
    // fast-refresh rule does not apply to them.
    files: ["apps/public-relay/**/*.{ts,tsx}"],
    rules: {
      "react-refresh/only-export-components": "off",
    },
  },
];
