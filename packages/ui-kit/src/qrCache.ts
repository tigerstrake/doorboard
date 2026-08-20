import QRCode from "qrcode";

/**
 * QR encoding, prepared ahead of time and never computed twice for the same code.
 *
 * On the door the QR took long enough to appear that it read as the panel hanging.
 * Measured on the Pi, none of it was the backend: minting an enrollment invite is
 * 16-44 ms and the visitor token is under 2 ms. The cost was all client-side, in two
 * places this module removes:
 *
 * 1. **First use paid for loading the encoder.** door-ui is served by the Vite dev
 *    server on the door, so `qrcode` is fetched and transformed on demand the first time
 *    a QR is rendered rather than being in a bundle. {@link warmQrEncoder} pays that once
 *    at startup, while nobody is waiting.
 * 2. **Re-showing a code recomputed it.** The visitor URL is stable for its whole
 *    token lifetime and the doorpad renders it every time somebody opens the screen.
 *
 * Deliberately not a pre-*mint*: an enrollment URL carries a single-use secret, so
 * speculatively creating one would put a live credential on a screen nobody asked for
 * and spend the hourly cap (ADR-0019 §3). Only the encoding is prepared early; the
 * secret is still minted on demand.
 */

export interface QrOptions {
  size: number;
}

const CACHE_LIMIT = 24;
const cache = new Map<string, string>();

const keyFor = (url: string, { size }: QrOptions): string => `${size}:${url}`;

function remember(key: string, dataUrl: string): void {
  // Bounded so a kiosk running for weeks cannot grow this without limit. Insertion
  // order is eviction order: the oldest code goes, which for a door is the one least
  // likely to be shown again.
  if (cache.size >= CACHE_LIMIT) {
    const oldest = cache.keys().next().value;
    if (oldest !== undefined) cache.delete(oldest);
  }
  cache.set(key, dataUrl);
}

/** The cached encoding for this code, if it has been generated before. */
export function cachedQr(url: string, options: QrOptions): string | null {
  return cache.get(keyFor(url, options)) ?? null;
}

export async function encodeQr(url: string, options: QrOptions): Promise<string> {
  const key = keyFor(url, options);
  const hit = cache.get(key);
  if (hit) return hit;
  const generated = await QRCode.toDataURL(url, {
    width: options.size,
    margin: 1,
    errorCorrectionLevel: "M",
    color: { dark: "#111111", light: "#ffffff" },
  });
  remember(key, generated);
  return generated;
}

let warmed = false;

/**
 * Load and exercise the encoder before anything needs it.
 *
 * Call once on kiosk startup. Failures are swallowed on purpose: this is an
 * optimisation, and a door that cannot pre-warm must still be able to show a QR.
 */
export async function warmQrEncoder(): Promise<void> {
  if (warmed) return;
  warmed = true;
  try {
    // Encoded and thrown away — the point is the module load and the first JIT pass,
    // not the result. Sized like the real ones so the same code paths run.
    await QRCode.toDataURL("https://doorboard.invalid/warmup", { width: 320, margin: 1 });
  } catch {
    /* the first real QR simply pays the cost instead */
  }
}
