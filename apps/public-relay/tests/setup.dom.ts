/**
 * Browser shims for the component suites.
 *
 * jsdom stops short of several APIs this app depends on, and each gap fails in a
 * way that looks like a component bug rather than a missing shim:
 *
 *  - `crypto.subtle` is absent, so the E-10 fingerprint check throws and every
 *    page renders "Cannot enrol" before a test can reach it. Node's WebCrypto is
 *    the same implementation a browser would provide, so the sealing path under
 *    test is the real one.
 *  - `getContext`, `toBlob`, `URL.createObjectURL` and `HTMLMediaElement.play`
 *    all throw "Not implemented", which would make the entire capture step
 *    untestable — and capture is where the black-preview bug lived.
 *  - `videoWidth` is a hardcoded 0, and the component treats 0 as "metadata has
 *    not arrived" (correctly — that is what a browser reports before it has).
 *    Here it is a getter over `data-test-video-width` so a test can say when the
 *    preview went live, per element, without a global.
 *
 * Nothing here fakes app logic. Everything stubbed is a browser capability.
 */
import { webcrypto } from "node:crypto";

if (!globalThis.crypto?.subtle) {
  Object.defineProperty(globalThis, "crypto", {
    value: webcrypto,
    configurable: true,
    writable: true,
  });
}

// The rest only applies under jsdom; the node-environment suites skip it.
if (typeof HTMLCanvasElement !== "undefined") {
  HTMLCanvasElement.prototype.getContext = function getContext() {
    return { drawImage() {} } as unknown as CanvasRenderingContext2D;
  } as unknown as HTMLCanvasElement["getContext"];

  HTMLCanvasElement.prototype.toBlob = function toBlob(callback: BlobCallback) {
    // The bytes do not matter: what the photo turns into on the wire is covered
    // by the cross-language seal vector (P-12), not here.
    callback(new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xd9])], { type: "image/jpeg" }));
  };
}

if (typeof HTMLMediaElement !== "undefined") {
  HTMLMediaElement.prototype.play = function play() {
    return Promise.resolve();
  };
}

if (typeof HTMLVideoElement !== "undefined") {
  for (const [property, attribute] of [
    ["videoWidth", "testVideoWidth"],
    ["videoHeight", "testVideoHeight"],
  ] as const) {
    Object.defineProperty(HTMLVideoElement.prototype, property, {
      configurable: true,
      get(this: HTMLVideoElement) {
        return Number(this.dataset[attribute] ?? 0);
      },
    });
  }
}

if (typeof URL !== "undefined" && !URL.createObjectURL) {
  let counter = 0;
  URL.createObjectURL = () => `blob:test/${(counter += 1)}`;
  URL.revokeObjectURL = () => {};
}
