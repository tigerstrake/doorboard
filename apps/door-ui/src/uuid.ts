// `crypto.randomUUID()` is only defined in a *secure context* (HTTPS, or a
// localhost origin). The kiosk loads over `http://localhost:5173`, which is a
// secure context, so it works there. The visitor page, however, is opened on a
// phone over `http://<lan-ip>:5173` (plain HTTP to a LAN IP) — that is NOT a
// secure context, so `crypto.randomUUID` is `undefined` and calling it throws,
// which crashes the whole React app to a blank screen.
//
// `crypto.getRandomValues()` — unlike `randomUUID`/`crypto.subtle` — IS available
// in insecure contexts, so we build an RFC-4122 v4 UUID from it as a fallback.
// The native path is preferred so the kiosk's behavior is unchanged.
export function safeRandomUUID(): string {
  const c: Crypto | undefined = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID();
  }

  const bytes = new Uint8Array(16);
  if (c && typeof c.getRandomValues === "function") {
    c.getRandomValues(bytes);
  } else {
    for (let i = 0; i < 16; i += 1) {
      bytes[i] = Math.floor(Math.random() * 256);
    }
  }
  // Version (4) and variant (10xx) bits per RFC 4122.
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;

  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0"));
  return (
    `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-` +
    `${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10, 16).join("")}`
  );
}
