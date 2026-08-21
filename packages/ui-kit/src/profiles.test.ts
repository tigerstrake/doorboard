import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { DEFAULT_ACCENT, PROFILE_COLORS, accentInk, profileAccent } from "./profiles";

/**
 * The catalogue is duplicated across a language boundary, so the duplication is what
 * gets tested. Without this the failure mode is silent: door-visiond gains a profile,
 * somebody enrols on it, and their greeting renders in the default accent forever.
 */
const CATALOG_PY = fileURLToPath(
  new URL("../../../apps/door-visiond/src/door_visiond/enrollment.py", import.meta.url)
);

function pythonCatalog(): Record<string, string> {
  const source = readFileSync(CATALOG_PY, "utf8");
  const block = source.match(/PROFILE_CATALOG[^=]*=\s*\(([\s\S]*?)\n\)/);
  if (!block) throw new Error(`PROFILE_CATALOG not found in ${CATALOG_PY}`);
  const entries = [...block[1].matchAll(/\(\s*"([^"]+)"\s*,\s*"(#[0-9a-fA-F]{3,8})"\s*\)/g)];
  if (entries.length === 0) throw new Error("PROFILE_CATALOG parsed but held no entries");
  return Object.fromEntries(entries.map(([, id, color]) => [id, color.toLowerCase()]));
}

describe("profile catalogue", () => {
  it("matches door-visiond's PROFILE_CATALOG exactly", () => {
    const fromPython = pythonCatalog();
    const fromTs = Object.fromEntries(
      Object.entries(PROFILE_COLORS).map(([id, color]) => [id, color.toLowerCase()])
    );
    expect(fromTs).toEqual(fromPython);
  });

  it("resolves a known profile to the colour chosen at onboarding", () => {
    expect(profileAccent("warm_amber")).toBe("#ffb300");
    expect(profileAccent("blue_wave")).toBe("#3a86ff");
  });

  it("falls back rather than throwing for an absent or unknown profile", () => {
    // A door on a newer catalogue than the kiosk bundle must still greet by name.
    expect(profileAccent(null)).toBe(DEFAULT_ACCENT);
    expect(profileAccent(undefined)).toBe(DEFAULT_ACCENT);
    expect(profileAccent("teal_unknown_from_a_newer_door")).toBe(DEFAULT_ACCENT);
  });

  it("picks readable ink for both ends of the catalogue", () => {
    // cool_white is near-white: the case where white-on-accent would be unreadable.
    expect(accentInk(profileAccent("cool_white"))).toBe("#12161c");
    expect(accentInk(profileAccent("violet_dusk"))).toBe("#ffffff");
  });

  it("gives every cataloged profile readable ink", () => {
    for (const [id, color] of Object.entries(PROFILE_COLORS)) {
      expect(["#12161c", "#ffffff"], `${id} (${color})`).toContain(accentInk(color));
    }
  });
});
