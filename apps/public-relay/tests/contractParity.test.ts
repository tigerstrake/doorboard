/**
 * Contract parity (ADR-0016 E-13).
 *
 * `lib/contracts.ts` redeclares the relay wire shapes so this app can build
 * standalone. That redeclaration is only safe if it cannot silently drift from
 * the generated source of truth, so this test reads
 * `packages/contracts/types/index.ts` off disk — no import, no dependency — and
 * compares the two field by field.
 *
 * When this fails, the generated file is right and `lib/contracts.ts` is stale.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const GENERATED = path.join(__dirname, "..", "..", "..", "packages", "contracts", "types", "index.ts");
const LOCAL = path.join(__dirname, "..", "lib", "contracts.ts");

/** Field name -> declared type text, for one `export interface` in a .ts source. */
function parseInterface(source: string, name: string): Map<string, string> | null {
  const match = new RegExp(`export interface ${name} \\{([^}]*)\\}`, "m").exec(source);
  if (!match?.[1]) return null;
  const fields = new Map<string, string>();
  for (const rawLine of match[1].split("\n")) {
    const line = rawLine.trim();
    if (line.length === 0 || line.startsWith("//") || line.startsWith("*") || line.startsWith("/*")) {
      continue;
    }
    const field = /^([A-Za-z_][A-Za-z0-9_]*)(\??):\s*(.+?);?$/.exec(line);
    if (!field?.[1] || !field[3]) continue;
    // Normalise whitespace and the optional marker so formatting differences
    // between the generator and hand-written code are not treated as drift.
    fields.set(`${field[1]}${field[2]}`, field[3].replace(/\s+/g, " ").replace(/;$/, "").trim());
  }
  return fields;
}

/** Every relay shape the Pi and the phone both have to agree on. */
const SHARED_SHAPES = [
  "SealedProfile",
  "SealedManifest",
  "SealedItem",
  "SealedBundle",
  "DoorKeyPublication",
  "InviteRegistration",
  "InvitePublicState",
  "BundleSubmitAccepted",
  "BundleStatus",
  "PickupItem",
  "PickupBatch",
  "PickupAck",
  "RelayHealth",
] as const;

describe("relay contract parity with packages/contracts", () => {
  const generated = readFileSync(GENERATED, "utf-8");
  const local = readFileSync(LOCAL, "utf-8");

  it.each(SHARED_SHAPES)("%s matches the generated declaration", (name) => {
    const fromGenerated = parseInterface(generated, name);
    const fromLocal = parseInterface(local, name);

    expect(fromGenerated, `${name} is missing from the generated contracts`).not.toBeNull();
    expect(fromLocal, `${name} is missing from lib/contracts.ts`).not.toBeNull();

    // Base64Url is an alias for string on this side; normalise before comparing.
    const normalise = (fields: Map<string, string>) =>
      Object.fromEntries(
        [...fields.entries()]
          .map(([key, value]) => [key, value.replace(/\bBase64Url\b/g, "string")])
          .sort(),
      );

    expect(normalise(fromLocal!)).toEqual(normalise(fromGenerated!));
  });

  it("pins the seal suite and HKDF info prefix to the Python constants", () => {
    // These live in enrollment_relay.py rather than the generated .ts, so assert
    // against the Python source directly.
    const pySource = readFileSync(
      path.join(
        __dirname,
        "..",
        "..",
        "..",
        "packages",
        "contracts",
        "src",
        "doorboard_contracts",
        "enrollment_relay.py",
      ),
      "utf-8",
    );
    expect(pySource).toContain('SEAL_SUITE: str = "ecies-p256-hkdf-sha256-aes256gcm"');
    expect(pySource).toContain('SEAL_INFO_PREFIX: str = "doorboard/enroll-relay/v1"');
    expect(local).toContain('SEAL_SUITE = "ecies-p256-hkdf-sha256-aes256gcm"');
    expect(local).toContain('SEAL_INFO_PREFIX = "doorboard/enroll-relay/v1"');
  });

  it("keeps display_name and invite_secret out of every relay-visible shape", () => {
    // The manifest is sealed plaintext; everything else crosses the relay and must
    // not be able to carry nominal data (E-13). This mirrors the Python-side test.
    for (const name of SHARED_SHAPES) {
      if (name === "SealedManifest" || name === "SealedProfile") continue;
      const fields = parseInterface(local, name);
      expect([...(fields?.keys() ?? [])]).not.toContain("display_name");
      expect([...(fields?.keys() ?? [])]).not.toContain("invite_secret");
    }
  });
});
