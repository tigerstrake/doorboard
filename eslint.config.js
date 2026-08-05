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
      "**/next-env.d.ts",
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
