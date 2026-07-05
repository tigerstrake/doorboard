/**
 * Generates an RFC-9562 compliant UUIDv7.
 *
 * UUIDv7 layout:
 * - 48 bits: Unix timestamp in milliseconds
 * - 4 bits: Version (0111)
 * - 12 bits: Random/sequence data
 * - 2 bits: Variant (10)
 * - 62 bits: Random data
 *
 * @returns A 36-character string representation of the UUIDv7.
 */
export function uuidv7(): string {
  const timestamp = Date.now();
  
  // 48-bit timestamp represented as 12 hex characters
  const tsHex = timestamp.toString(16).padStart(12, "0");
  
  // Version 7 (4 bits): '7'
  // 12 bits of randomness (3 hex characters)
  const randA = Math.floor(Math.random() * 0x1000).toString(16).padStart(3, "0");
  
  // Variant (2 bits): 10xx -> 0x8000 to 0xbfff (4 hex characters)
  // 14 bits of randomness (0x8000 | 14 bits)
  const randB = (0x8000 | Math.floor(Math.random() * 0x4000)).toString(16).padStart(4, "0");
  
  // 48 bits of randomness (12 hex characters)
  const randC = Array.from({ length: 12 }, () => Math.floor(Math.random() * 16).toString(16)).join("");
  
  return `${tsHex.slice(0, 8)}-${tsHex.slice(8, 12)}-7${randA}-${randB}-${randC}`;
}
