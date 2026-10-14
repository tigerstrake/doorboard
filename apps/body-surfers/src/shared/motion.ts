// prefers-reduced-motion, read live (the OS setting can change while the page is open).
// Safe outside the browser (tests, workers): reports false there.

let query: MediaQueryList | null | undefined;

/** True when the player asked the OS for less motion (no shake, FOV kicks, pops, flashes). */
export function reducedMotion(): boolean {
  if (query === undefined) {
    try {
      query = typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : null;
    } catch {
      query = null;
    }
  }
  return query?.matches ?? false;
}
