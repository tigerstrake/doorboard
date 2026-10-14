/* global process, console */
// Copies the built Body Surfers game into public/games/body-surfers, so the kiosk
// build (and the dev server) serves it at /games/body-surfers/ (ADR-0049).
//
//   node scripts/copy-games.mjs             copy if the game is built, else warn
//   node scripts/copy-games.mjs --required  fail when it is not (the production build)
import { cpSync, existsSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "../body-surfers/dist");
const dest = join(root, "public/games/body-surfers");
const required = process.argv.includes("--required");

if (!existsSync(join(src, "index.html"))) {
  const message = `Body Surfers is not built (${src}); run: pnpm --filter @doorboard/body-surfers build`;
  if (required) {
    console.error(message);
    process.exit(1);
  }
  console.warn(`${message} -- /games/body-surfers/ will 404 until then`);
  process.exit(0);
}
rmSync(dest, { recursive: true, force: true });
// hall.html is the laptop booth's second screen; the door has no use for it.
cpSync(src, dest, { recursive: true, filter: (f) => !f.endsWith("hall.html") });
console.log(`copied Body Surfers -> ${dest}`);
