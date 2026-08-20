/**
 * The profile catalogue: the colour each enrolled person chose during onboarding.
 *
 * MIRRORS `PROFILE_CATALOG` in
 * `apps/door-visiond/src/door_visiond/enrollment.py`, which is the source of truth
 * (the ESP32 effect ids come from the same table, T-103). `profiles.test.ts` parses
 * that file and fails if the two drift, so adding a profile there without adding it
 * here is a test failure rather than a colour that silently stops appearing.
 *
 * Why a mirror rather than a field on the event: `vision.identity_stable` already
 * carries `profile_id`, and the colour is a pure function of it. Adding `color` to the
 * payload would be a change to the normative event catalogue (ADR-0003 discipline) to
 * transmit something both ends can already derive.
 *
 * Before this existed, `GreetingBanner` matched `profileId` against the substrings
 * "owner" and "roommate" — which no cataloged id contains — so every real enrollee
 * rendered in the same generic style and the colour picked at onboarding reached
 * nothing but the door's LED ring.
 */

export const PROFILE_COLORS: Readonly<Record<string, string>> = Object.freeze({
  warm_amber: "#ffb300",
  blue_wave: "#3a86ff",
  green_pulse: "#3ddc84",
  violet_dusk: "#9b5de5",
  coral_glow: "#ff6b5e",
  cool_white: "#e8eef5",
});

/** Accent used when nobody is recognised, or for a profile id we do not know. */
export const DEFAULT_ACCENT = "#6ea8ff";

/**
 * The accent colour for a profile id, or the default.
 *
 * Unknown ids fall back rather than throwing: an id can arrive from a door running a
 * newer catalogue than the kiosk bundle, and a missing colour must not blank the
 * greeting that carries the person's name.
 */
export function profileAccent(profileId: string | null | undefined): string {
  if (!profileId) return DEFAULT_ACCENT;
  return PROFILE_COLORS[profileId] ?? DEFAULT_ACCENT;
}

/**
 * Readable ink for text laid over {@link profileAccent}.
 *
 * `cool_white` is a near-white accent, so white-on-white is a real outcome here rather
 * than a hypothetical one. Uses the WCAG relative-luminance threshold so the choice is
 * a contrast decision and not a hand-maintained list of exceptions.
 */
export function accentInk(accent: string): string {
  const hex = accent.replace("#", "");
  const full =
    hex.length === 3
      ? hex
          .split("")
          .map((c) => c + c)
          .join("")
      : hex;
  const channel = (offset: number): number => {
    const srgb = parseInt(full.slice(offset, offset + 2), 16) / 255;
    return srgb <= 0.03928 ? srgb / 12.92 : Math.pow((srgb + 0.055) / 1.055, 2.4);
  };
  const luminance = 0.2126 * channel(0) + 0.7152 * channel(2) + 0.0722 * channel(4);
  return luminance > 0.45 ? "#12161c" : "#ffffff";
}
