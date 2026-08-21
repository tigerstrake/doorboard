// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import path from "node:path";

import React from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import EnrollFlow, { outcomeMessage, uploadErrorMessage } from "@/app/e/[token]/EnrollFlow";
import { base64UrlDecode, base64UrlEncode, fingerprintFor } from "@/lib/seal";
import { SEAL_INFO_PREFIX, SEAL_SUITE } from "@/lib/contracts";

/**
 * The phone enrolment page (ADR-0016).
 *
 * This file exists because of two bugs that reached a real phone and that no test
 * here could have missed:
 *
 *  - the camera preview stayed black, because the stream was attached inside a
 *    `requestAnimationFrame` that raced React's commit, so the `<video>` did not
 *    exist yet and `srcObject` was set on nothing;
 *  - a colour clash surfaced as "Enrolment did not complete", because
 *    `internal_error` was absent from the outcome map.
 *
 * Both were in the one part of the app with no component coverage at all. So the
 * emphasis here is on the two things that broke — the camera lifecycle, and every
 * failure code having somewhere to land — plus the invariant that must never
 * break quietly: nothing leaves the phone unsealed.
 *
 * The door keypair is generated for real, and the bundle the page uploads is
 * opened with the matching private key. That means the seal, the AAD binding and
 * the E-10 fingerprint check are exercised as written rather than mocked past.
 */

const TOKEN = `inv_${"a".repeat(22)}.c2VjcmV0LXZhbHVlLWhlcmU`;
const INVITE_SECRET = "c2VjcmV0LXZhbHVlLWhlcmU";
const DOOR_KEY_ID = `dky_${"c".repeat(22)}`;
const CONSENT_TEXT =
  "# Face-recognition consent statement\n\nVersion: v3\n\nBy enrolling you agree to be greeted by name.";

let doorPrivateKey: CryptoKey;
let doorPublicKeyB64: string;
let doorFingerprint: string;

beforeAll(async () => {
  const pair = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, [
    "deriveBits",
  ]);
  doorPrivateKey = pair.privateKey;
  const raw = new Uint8Array(await crypto.subtle.exportKey("raw", pair.publicKey));
  // Built with the app's own encoder, so the fixture cannot disagree with the
  // page about how a key is spelled on the wire.
  doorPublicKeyB64 = base64UrlEncode(raw);
  doorFingerprint = await fingerprintFor(raw);
});

function doorKeyBody() {
  return {
    door_key_id: DOOR_KEY_ID,
    suite: SEAL_SUITE,
    public_key: doorPublicKeyB64,
    fingerprint: doorFingerprint,
    consent_version: "v3",
    consent_text: CONSENT_TEXT,
    published_at: new Date().toISOString(),
  };
}

interface Routes {
  invite?: unknown;
  doorKeyStatus?: number;
  submitStatus?: number;
  submitBody?: unknown;
}

interface Call {
  method: string;
  url: string;
  body: string | null;
}

/** Stub the three routes the page talks to, recording every request. */
function mockApi(routes: Routes = {}): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown, init?: RequestInit) => {
      const url = String(input);
      calls.push({
        method: (init?.method ?? "GET").toUpperCase(),
        url,
        body: typeof init?.body === "string" ? init.body : null,
      });

      if (url.includes("/submit")) {
        const status = routes.submitStatus ?? 202;
        return {
          ok: status < 400,
          status,
          json: async () =>
            routes.submitBody ?? { bundle_id: "bnd_x", status: "pending" },
        } as Response;
      }
      if (url.includes("/api/door-key")) {
        const status = routes.doorKeyStatus ?? 200;
        return { ok: status < 400, status, json: async () => doorKeyBody() } as Response;
      }
      if (url.includes("/api/status/")) {
        return { ok: true, status: 200, json: async () => ({ status: "pending", reason: null }) } as Response;
      }
      return {
        ok: true,
        status: 200,
        json: async () =>
          routes.invite ?? { invite_id: `inv_${"a".repeat(22)}`, status: "open", max_images: 5 },
      } as Response;
    }),
  );
  return calls;
}

/** A getUserMedia whose stream reports whether its tracks were stopped. */
function mockCamera() {
  const stops: number[] = [];
  let issued = 0;
  const getUserMedia = vi.fn(async () => {
    const id = (issued += 1);
    return { getTracks: () => [{ stop: () => stops.push(id) }] } as unknown as MediaStream;
  });
  Object.defineProperty(navigator, "mediaDevices", {
    value: { getUserMedia },
    configurable: true,
  });
  return { getUserMedia, stops };
}

function failingCamera(error: unknown) {
  const getUserMedia = vi.fn(async () => Promise.reject(error));
  Object.defineProperty(navigator, "mediaDevices", {
    value: { getUserMedia },
    configurable: true,
  });
  return getUserMedia;
}

beforeEach(() => {
  // The fingerprint travels in the URL fragment, which never reaches a server.
  window.location.hash = `#k=${doorFingerprint}`;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

async function reachConsent() {
  render(<EnrollFlow token={TOKEN} />);
  await waitFor(() => expect(screen.getByText("Please read and agree")).toBeTruthy());
}

/** Tell the page its preview has gone live, the way a browser would. */
function markPreviewLive() {
  const video = document.querySelector("video");
  if (!video) throw new Error("no <video> rendered");
  video.dataset.testVideoWidth = "1280";
  video.dataset.testVideoHeight = "960";
  fireEvent(video, new Event("canplay"));
  return video;
}

async function reachCapture() {
  const camera = mockCamera();
  await reachConsent();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByText("Continue to photos"));
  await waitFor(() => expect(screen.getByText(/Take 3 photos/)).toBeTruthy());
  return camera;
}

async function takeAllPhotos() {
  markPreviewLive();
  for (let index = 1; index <= 3; index += 1) {
    fireEvent.click(screen.getByText(`Take photo ${index} of 3`));
    await waitFor(() =>
      expect(document.querySelectorAll(".thumbs img").length).toBe(index),
    );
  }
}

describe("invite and key verification", () => {
  it("shows the consent statement verbatim, as the door published it", async () => {
    mockApi();
    await reachConsent();
    // E-7: the door's wording, not a paraphrase written here. Compared against
    // textContent rather than via getByText, which collapses whitespace and would
    // pass on reflowed text — "verbatim" has to include the line breaks.
    expect(document.querySelector(".consent")?.textContent).toBe(CONSENT_TEXT);
    expect(screen.getByText(/version v3/)).toBeTruthy();
  });

  it("blocks a consumed invite before asking for anything", async () => {
    mockApi({ invite: { invite_id: "", status: "consumed", max_images: 5 } });
    render(<EnrollFlow token={TOKEN} />);
    await waitFor(() => expect(screen.getByText("Cannot enrol")).toBeTruthy());
    expect(screen.getByText(/already been used/)).toBeTruthy();
  });

  it("blocks when the door has not checked in", async () => {
    mockApi({ doorKeyStatus: 503 });
    render(<EnrollFlow token={TOKEN} />);
    await waitFor(() => expect(screen.getByText(/has not checked in/)).toBeTruthy());
  });

  it("refuses to proceed when the QR fragment is missing (E-10)", async () => {
    window.location.hash = "";
    mockApi();
    render(<EnrollFlow token={TOKEN} />);
    await waitFor(() => expect(screen.getByText(/missing its security check/)).toBeTruthy());
  });

  it("refuses, and uploads nothing, when the key does not match the fragment (E-10)", async () => {
    window.location.hash = "#k=Zm9yZ2VkLWZpbmdlcnByaW50";
    const calls = mockApi();
    render(<EnrollFlow token={TOKEN} />);
    await waitFor(() =>
      expect(screen.getByText(/does not match the code you scanned/)).toBeTruthy(),
    );
    // The load-bearing half of E-10: refusing to *say* yes is not enough.
    expect(calls.filter((call) => call.url.includes("/submit"))).toHaveLength(0);
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("keeps the camera behind the consent checkbox", async () => {
    mockApi();
    const { getUserMedia } = mockCamera();
    await reachConsent();

    const button = screen.getByText("Continue to photos") as HTMLButtonElement;
    expect(button.disabled).toBe(true);

    fireEvent.click(screen.getByRole("checkbox"));
    expect(button.disabled).toBe(false);
    // Disabled meant disabled: nothing opened the camera while it was.
    expect(getUserMedia).not.toHaveBeenCalled();
  });
});

describe("the camera", () => {
  it("attaches the stream to the <video> once the step has committed", async () => {
    // The black-preview regression. Attaching used to happen in a
    // requestAnimationFrame that could run before React committed the capture
    // step, so videoRef.current was null and srcObject was never set.
    mockApi();
    const { getUserMedia } = await reachCapture();
    const stream = await getUserMedia.mock.results[0]!.value;

    const video = document.querySelector("video") as HTMLVideoElement;
    expect(video).not.toBeNull();
    expect(video.srcObject).toBe(stream);
  });

  it("holds the shutter until the preview is genuinely live", async () => {
    mockApi();
    await reachCapture();

    // videoWidth is still 0 — metadata has not arrived. This is the exact state
    // in which tapping produced "the camera is not ready yet".
    const before = screen.getByText("Starting the camera…", { selector: "button" });
    expect((before as HTMLButtonElement).disabled).toBe(true);

    markPreviewLive();
    await waitFor(() =>
      expect((screen.getByText("Take photo 1 of 3") as HTMLButtonElement).disabled).toBe(false),
    );
  });

  it("explains a blocked permission specifically", async () => {
    mockApi();
    failingCamera(new DOMException("denied", "NotAllowedError"));
    await reachConsent();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByText("Continue to photos"));

    await waitFor(() => expect(screen.getByText(/Camera access was blocked/)).toBeTruthy());
    // Still on consent, so they can grant permission and retry in place.
    expect(screen.getByText("Please read and agree")).toBeTruthy();
  });

  it("distinguishes a camera in use from a blocked one", async () => {
    mockApi();
    failingCamera(new Error("device busy"));
    await reachConsent();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByText("Continue to photos"));

    await waitFor(() => expect(screen.getByText(/in use by another app/)).toBeTruthy());
    expect(screen.queryByText(/Camera access was blocked/)).toBeNull();
  });

  it("releases the old track before opening a second one", async () => {
    // Re-entering capture via "Back to photos" used to open a second camera track
    // without closing the first: some phones refuse outright, others answer with
    // a black frame.
    mockApi();
    const { getUserMedia, stops } = await reachCapture();
    await takeAllPhotos();

    fireEvent.click(screen.getByText("Looks good, continue"));
    await waitFor(() => expect(screen.getByText(/How should the door greet you/)).toBeTruthy());

    fireEvent.click(screen.getByText("Back to photos"));
    // getUserMedia resolves before React commits the step, so wait for the step
    // itself — not the call — or the <video> may not exist yet.
    await waitFor(() => expect(screen.getByText(/Take 3 photos/)).toBeTruthy());
    expect(getUserMedia).toHaveBeenCalledTimes(2);

    // The first stream was stopped, and stopped before the second one opened.
    expect(stops).toEqual([1]);
    const video = markPreviewLive();
    expect(video.srcObject).toBe(await getUserMedia.mock.results[1]!.value);
  });
});

describe("what leaves the phone", () => {
  /** The door side of the seal, mirroring relay_seal.py's opener. */
  async function openBundle(bundle: {
    bundle_id: string;
    door_key_id: string;
    ephemeral_public_key: string;
    salt: string;
    items: { index: number; nonce: string; ciphertext: string }[];
  }): Promise<Uint8Array[]> {
    const ephemeral = await crypto.subtle.importKey(
      "raw",
      base64UrlDecode(bundle.ephemeral_public_key) as BufferSource,
      { name: "ECDH", namedCurve: "P-256" },
      false,
      [],
    );
    const shared = await crypto.subtle.deriveBits(
      { name: "ECDH", public: ephemeral },
      doorPrivateKey,
      256,
    );
    const hkdf = await crypto.subtle.importKey("raw", shared, "HKDF", false, ["deriveBits"]);
    const derived = await crypto.subtle.deriveBits(
      {
        name: "HKDF",
        hash: "SHA-256",
        salt: base64UrlDecode(bundle.salt) as BufferSource,
        info: new TextEncoder().encode(
          `${SEAL_INFO_PREFIX}|${bundle.door_key_id}|${bundle.bundle_id}`,
        ) as BufferSource,
      },
      hkdf,
      256,
    );
    const key = await crypto.subtle.importKey("raw", derived, "AES-GCM", false, ["decrypt"]);

    const opened: Uint8Array[] = [];
    for (const item of bundle.items) {
      const plaintext = await crypto.subtle.decrypt(
        {
          name: "AES-GCM",
          iv: base64UrlDecode(item.nonce) as BufferSource,
          additionalData: new TextEncoder().encode(
            `${bundle.bundle_id}:${bundle.door_key_id}:${item.index}`,
          ) as BufferSource,
          tagLength: 128,
        },
        key,
        base64UrlDecode(item.ciphertext) as BufferSource,
      );
      opened.push(new Uint8Array(plaintext));
    }
    return opened;
  }

  async function submitEnrolment(calls: Call[]) {
    await reachCapture();
    await takeAllPhotos();
    fireEvent.click(screen.getByText("Looks good, continue"));
    await waitFor(() => expect(screen.getByText(/How should the door greet you/)).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "Tiger" } });
    fireEvent.click(screen.getByLabelText("Green"));
    fireEvent.click(screen.getByText("Encrypt and send"));

    await waitFor(() => expect(calls.some((call) => call.url.includes("/submit"))).toBe(true));
    return JSON.parse(calls.find((call) => call.url.includes("/submit"))!.body!);
  }

  it("uploads a bundle the door can open, and nothing else", async () => {
    const calls = mockApi();
    const bundle = await submitEnrolment(calls);

    expect(bundle.suite).toBe(SEAL_SUITE);
    expect(bundle.door_key_id).toBe(DOOR_KEY_ID);
    // 1 manifest + 3 photos.
    expect(bundle.items).toHaveLength(4);

    const opened = await openBundle(bundle);
    const manifest = JSON.parse(new TextDecoder().decode(opened[0]!));
    expect(manifest.display_name).toBe("Tiger");
    expect(manifest.invite_secret).toBe(INVITE_SECRET);
    expect(manifest.consent_version).toBe("v3");
    expect(manifest.consent_confirmed).toBe(true);
    expect(manifest.profile.profile_id).toBe("green_pulse");
    expect(manifest.image_count).toBe(3);
    expect(opened.slice(1)).toHaveLength(3);
  });

  it("puts the name and the invite secret nowhere the relay can read them (E-8)", async () => {
    const calls = mockApi();
    const bundle = await submitEnrolment(calls);
    const wire = JSON.stringify(bundle);

    // The manifest is item 0 of the AEAD envelope, not an upload field, so the
    // relay cannot see any of this even in principle.
    for (const secret of ["Tiger", INVITE_SECRET, "green_pulse", "#3ddc84"]) {
      expect(wire).not.toContain(secret);
    }
    // Only the fields the relay's allow-list parser accepts.
    expect(Object.keys(bundle).sort()).toEqual([
      "bundle_id",
      "door_key_id",
      "ephemeral_public_key",
      "invite_id",
      "items",
      "salt",
      "suite",
      "v",
    ]);
    expect(Object.keys(bundle.items[0]).sort()).toEqual(["ciphertext", "index", "nonce"]);
  });

  it("binds each item to its bundle, key and position", async () => {
    const calls = mockApi();
    const bundle = await submitEnrolment(calls);

    // Transplanting item 1 into position 2 must not decrypt: the index is in the
    // AAD, so photos cannot be reordered or swapped between bundles.
    const reordered = {
      ...bundle,
      items: [bundle.items[0], { ...bundle.items[1], index: 2 }],
    };
    await expect(openBundle(reordered)).rejects.toThrow();
  });

  it("keeps the enrollee on the details step when the upload is refused", async () => {
    const calls = mockApi({ submitStatus: 409, submitBody: { error: "invite_already_used" } });
    await submitEnrolment(calls);

    await waitFor(() => expect(screen.getByText(/already been used/)).toBeTruthy());
    // Their photos and name are still in hand, so a fresh QR is all they need.
    expect(screen.getByText(/How should the door greet you/)).toBeTruthy();
  });
});

describe("every failure code lands somewhere useful", () => {
  const REPO = path.join(__dirname, "..", "..", "..");

  /** Reasons the Pi can put in a pickup ack, read out of its source. */
  function pickupReasons(): string[] {
    const found = new Set<string>();
    for (const file of ["service.py", "enrollment.py"]) {
      const source = readFileSync(
        path.join(REPO, "apps", "door-visiond", "src", "door_visiond", file),
        "utf8",
      );
      for (const match of source.matchAll(/reason="([a-z_]+)"/g)) found.add(match[1]!);
      for (const match of source.matchAll(/InviteUnusableError\("([a-z_]+)"\)/g)) {
        found.add(match[1]!);
      }
    }
    // Not ack reasons: cache-invalidation causes that never reach a phone.
    found.delete("admin");
    found.delete("privacy_mode_cleared");
    return [...found].sort();
  }

  it("names every outcome the Pi can send", () => {
    const reasons = pickupReasons();
    // A guard on the scraper itself: a regex that silently matches nothing would
    // otherwise make this test pass by covering an empty set.
    expect(reasons).toContain("internal_error");
    expect(reasons.length).toBeGreaterThan(8);

    const unmapped = reasons.filter((reason) => outcomeMessage(reason).includes(`(${reason})`));
    expect(unmapped).toEqual([]);
  });

  it("covers the client-side outcomes the Pi never sends", () => {
    // Set by the page and the relay respectively, so the scraper cannot see them.
    for (const reason of ["timed_out", "bundle_expired"]) {
      expect(outcomeMessage(reason)).not.toContain(`(${reason})`);
    }
  });

  it("still echoes an unrecognised outcome rather than swallowing it", () => {
    // The colour-clash lesson: whatever the map misses, the phone must show
    // something an admin can act on.
    expect(outcomeMessage("something_new")).toContain("something_new");
  });

  it("says something actionable for every code the submit route returns", () => {
    const source = readFileSync(
      path.join(REPO, "apps", "public-relay", "app", "api", "enroll", "[token]", "submit", "route.ts"),
      "utf8",
    );
    const codes = [...source.matchAll(/jsonError\(\d+,\s*"([a-z_]+)"\)/g)].map((m) => m[1]!);
    expect(codes).toContain("rate_limited");

    for (const code of new Set(codes)) {
      const message = uploadErrorMessage(code);
      // Either specific advice, or a message that at least names the code.
      const generic = "The service would not accept the upload";
      expect(message.startsWith(generic) ? message.includes(`(${code})`) : true).toBe(true);
      expect(message.length).toBeGreaterThan(20);
    }
  });
});
