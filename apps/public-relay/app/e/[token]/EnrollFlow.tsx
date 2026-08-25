"use client";

/**
 * The phone-side enrolment flow (ADR-0016).
 *
 * Order of operations is a security property, not a UX preference:
 *
 *   1. check the invite is open
 *   2. fetch the door key and **verify it against the QR's fragment** (E-10)
 *   3. show the consent statement verbatim, as published by the Pi (E-7)
 *   4. capture photos with the phone camera
 *   5. seal name + consent + photos to the door key, then upload (E-8)
 *   6. poll for the door to collect and enrol
 *
 * Step 2 gates step 5: if the fingerprint does not match, sealing never happens
 * and nothing is uploaded. There is no "continue anyway".
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { DoorKeyPublication, InvitePublicState, SealedProfile } from "@/lib/contracts";
import { KeyFingerprintMismatch, assertKeyMatchesFingerprint, newBundleId, sealBundle } from "@/lib/seal";
import { INVITE_SECRET_HEADER } from "@/lib/validate";

type Step = "checking" | "blocked" | "consent" | "capture" | "details" | "sending" | "waiting" | "done";

/**
 * Read one parameter from the URL fragment. The invite secret (`s`) and the key fingerprint
 * (`k`) both live here (ADR-0043 §2, ADR-0016 §3): fragments are never sent to a server, so
 * neither reaches the relay through a request line.
 */
function hashParam(name: string): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.hash.replace(/^#/, "")).get(name);
}

/**
 * The invite id is the last path segment. On Cloudflare the page is a single static shell
 * served for every `/e/<id>` (ADR-0043 §1, public/_redirects), so the id is read from the
 * live path here rather than a route param — which keeps the door-built URL `/e/<id>#s=…`
 * unchanged. Tests pass it as a prop, which wins.
 */
function pathInviteId(): string {
  if (typeof window === "undefined") return "";
  const segments = window.location.pathname.split("/").filter(Boolean);
  return decodeURIComponent(segments[segments.length - 1] ?? "");
}

/** The effects catalogue the doorboard understands (T-103) — every id is a real firmware
 * effect (door-visiond PROFILE_CATALOG). The old amber/violet/coral/white ids were not, so
 * those lights silently fell back to blue; these are the firmware's six. */
const PROFILES: ReadonlyArray<{ id: string; color: string; name: string }> = [
  { id: "sunrise", color: "#ffb300", name: "Amber" },
  { id: "blue_wave", color: "#3a86ff", name: "Blue" },
  { id: "green_pulse", color: "#3ddc84", name: "Green" },
  { id: "mint_pulse", color: "#2ec4b6", name: "Mint" },
  { id: "rainbow", color: "#9b5de5", name: "Rainbow" },
  { id: "sparkle", color: "#e8eef5", name: "Sparkle" },
];

const POSES = [
  "Look straight at the camera.",
  "Turn your head slightly left.",
  "Turn your head slightly right.",
  "Tilt your chin down a little.",
  "One more, straight on.",
];

const MAX_EDGE_PX = 1000;
const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 3 * 60 * 1000;

export default function EnrollFlow({ inviteId: propInviteId }: { inviteId?: string }) {
  const inviteId = propInviteId ?? pathInviteId();
  const [step, setStep] = useState<Step>("checking");
  const [error, setError] = useState<string | null>(null);
  const [blockedReason, setBlockedReason] = useState<string | null>(null);
  const [invite, setInvite] = useState<InvitePublicState | null>(null);
  const [doorKey, setDoorKey] = useState<DoorKeyPublication | null>(null);
  const [consentChecked, setConsentChecked] = useState(false);
  const [photos, setPhotos] = useState<Uint8Array[]>([]);
  const [previews, setPreviews] = useState<string[]>([]);
  const [cameraReady, setCameraReady] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [profileId, setProfileId] = useState(PROFILES[0]!.id);
  // The colour is now the enrollee's own (ADR-0021), independent of which LED effect
  // the door ends up allocating. Two people may pick the same one.
  const [accentColor, setAccentColor] = useState(PROFILES[0]!.color);
  const [statusReason, setStatusReason] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const maxImages = invite?.max_images ?? 3;
  const targetPhotos = Math.min(3, maxImages);

  // -- step 1 + 2: invite state, then key verification --------------------

  useEffect(() => {
    let cancelled = false;

    async function prepare() {
      try {
        // Both halves of the security check now live in the fragment (ADR-0043 §2): the invite
        // secret (`s`) and the key fingerprint (`k`). Neither is ever sent to a server, so read
        // and require both here, before the first request.
        const secret = hashParam("s");
        const expectedFingerprint = hashParam("k");
        if (!secret || !expectedFingerprint) {
          setBlockedReason(
            "This link is missing its security check. Scan the QR code from the doorboard " +
              "again rather than copying the address by hand.",
          );
          setStep("blocked");
          return;
        }

        const inviteResp = await fetch(`/api/enroll/${encodeURIComponent(inviteId)}`, {
          cache: "no-store",
          headers: { [INVITE_SECRET_HEADER]: secret },
        });
        const inviteState = (await inviteResp.json()) as InvitePublicState;
        if (cancelled) return;

        if (inviteState.status !== "open") {
          setBlockedReason(
            {
              consumed: "This invitation has already been used. Ask for a fresh QR code.",
              expired: "This invitation has expired. Ask for a fresh QR code.",
              revoked: "This invitation was cancelled. Ask the household admin for a new one.",
              unknown: "This link is not valid. Check you scanned the whole QR code.",
            }[inviteState.status] ?? "This link cannot be used.",
          );
          setStep("blocked");
          return;
        }
        setInvite(inviteState);

        const keyResp = await fetch("/api/door-key", { cache: "no-store" });
        if (!keyResp.ok) {
          setBlockedReason(
            "The door device has not checked in, so there is nothing to send to yet. " +
              "Make sure it is powered on and online, then reload this page.",
          );
          setStep("blocked");
          return;
        }
        const key = (await keyResp.json()) as DoorKeyPublication;

        // The fragment never reaches a server, so this comparison is the one thing
        // a tampered relay cannot quietly satisfy (E-10).
        await assertKeyMatchesFingerprint(key.public_key, expectedFingerprint);

        if (cancelled) return;
        setDoorKey(key);
        setStep("consent");
      } catch (caught) {
        if (cancelled) return;
        if (caught instanceof KeyFingerprintMismatch) {
          setBlockedReason(caught.message);
        } else {
          setBlockedReason("Could not reach the enrolment service. Check your connection and reload.");
        }
        setStep("blocked");
      }
    }

    void prepare();
    return () => {
      cancelled = true;
    };
  }, [inviteId]);

  // -- camera lifecycle ---------------------------------------------------

  const stopCamera = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  useEffect(() => stopCamera, [stopCamera]);

  const startCamera = useCallback(async () => {
    setError(null);
    setCameraReady(false);
    try {
      // Release any previous stream first: re-entering this step (via "Back to
      // photos") would otherwise open a second camera track, which some phones
      // refuse outright and others answer with a black frame.
      stopCamera();
      streamRef.current = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 1280 } },
        audio: false,
      });
      // Attaching the stream happens in an effect, not here: the <video> does not
      // exist until React has committed this step, and there is no reliable way to
      // wait for that commit from inside an async handler.
      setStep("capture");
    } catch (caught) {
      const denied = caught instanceof DOMException && caught.name === "NotAllowedError";
      setError(
        denied
          ? "Camera access was blocked. Allow it for this site in your browser settings, then tap again — or enrol at the door instead."
          : "Could not open the camera. It may be in use by another app. Close that and tap again, or enrol at the door instead.",
      );
    }
  }, [stopCamera]);

  /**
   * Attach the live stream once the capture step is actually on screen.
   *
   * This must be an effect rather than a callback after `setStep`: effects run
   * after the DOM commit, so `videoRef.current` is guaranteed to exist. Doing it
   * in a `requestAnimationFrame` raced React's commit and left the preview black
   * with the stream never attached.
   */
  useEffect(() => {
    if (step !== "capture") return;
    const video = videoRef.current;
    const stream = streamRef.current;
    if (!video || !stream) return;

    video.srcObject = stream;
    const markReady = () => {
      // videoWidth is only trustworthy once metadata has arrived.
      if (video.videoWidth > 0) setCameraReady(true);
    };
    video.addEventListener("loadedmetadata", markReady);
    video.addEventListener("canplay", markReady);
    void video.play().catch(() => {
      setError("The camera preview could not start. Tap 'Back to photos' to try again.");
    });
    markReady();

    return () => {
      video.removeEventListener("loadedmetadata", markReady);
      video.removeEventListener("canplay", markReady);
    };
  }, [step]);

  const capture = useCallback(async () => {
    const video = videoRef.current;
    if (!video || video.videoWidth === 0) {
      setError("The camera is not ready yet — give it a moment and try again.");
      return;
    }

    const scale = Math.min(1, MAX_EDGE_PX / Math.max(video.videoWidth, video.videoHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    const context = canvas.getContext("2d");
    if (!context) {
      setError("This browser cannot process the photo. Try a different browser.");
      return;
    }
    context.drawImage(video, 0, 0, canvas.width, canvas.height);

    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.9),
    );
    if (!blob) {
      setError("Could not save the photo. Try again.");
      return;
    }

    const bytes = new Uint8Array(await blob.arrayBuffer());
    setPhotos((current) => [...current, bytes]);
    setPreviews((current) => [...current, URL.createObjectURL(blob)]);
    setError(null);
  }, []);

  const retakeAll = useCallback(() => {
    previews.forEach((url) => URL.revokeObjectURL(url));
    setPhotos([]);
    setPreviews([]);
  }, [previews]);

  // -- step 5: seal and upload --------------------------------------------

  const submit = useCallback(async () => {
    if (!doorKey || !invite) return;
    const trimmed = displayName.trim();
    if (trimmed.length === 0) {
      setError("Please enter the name the door should greet you by.");
      return;
    }

    setError(null);
    setStep("sending");
    stopCamera();

    try {
      const chosen = PROFILES.find((entry) => entry.id === profileId) ?? PROFILES[0]!;
      const profile: SealedProfile = {
        profile_id: chosen.id,
        color: chosen.color,
        sound: null,
        // Sent separately from `color`, which stays the catalogue colour of the LED
        // effect. The door may reassign that effect if it is taken; it must not move the
        // colour with it (ADR-0021).
        accent_color: accentColor,
      };
      const bundleId = newBundleId();

      const secret = hashParam("s") ?? "";
      const bundle = await sealBundle({
        doorPublicKey: doorKey.public_key,
        doorKeyId: doorKey.door_key_id,
        inviteId: invite.invite_id,
        bundleId,
        manifest: {
          invite_secret: secret,
          display_name: trimmed,
          consent_version: doorKey.consent_version,
          consent_confirmed: true,
          profile,
          captured_at: new Date().toISOString(),
          image_count: photos.length,
        },
        images: photos,
      });

      const resp = await fetch(`/api/enroll/${encodeURIComponent(inviteId)}/submit`, {
        method: "POST",
        headers: { "content-type": "application/json", [INVITE_SECRET_HEADER]: secret },
        body: JSON.stringify(bundle),
      });
      if (!resp.ok) {
        const body = (await resp.json().catch(() => ({}))) as { error?: string };
        setError(uploadErrorMessage(body.error));
        setStep("details");
        return;
      }

      // The photos have left the phone sealed; drop the plaintext copies.
      retakeAll();
      setStep("waiting");
      void pollUntilSettled(bundleId, setStep, setStatusReason);
    } catch {
      setError("Could not encrypt and send the photos. Please try again.");
      setStep("details");
    }
    // accentColor belongs here: without it `submit` closes over the colour as it was
    // when the callback was last created, so a visitor who adjusts the picker and sends
    // immediately would enrol with the previous colour (ADR-0021).
  }, [accentColor, doorKey, displayName, invite, photos, profileId, retakeAll, stopCamera, inviteId]);

  const stepIndex = useMemo(() => {
    const order: Step[] = ["consent", "capture", "details", "sending", "waiting", "done"];
    return Math.max(0, order.indexOf(step));
  }, [step]);

  // -- rendering ----------------------------------------------------------

  if (step === "checking") {
    return (
      <div className="center">
        <div className="spinner" />
        <p>Checking your invitation…</p>
      </div>
    );
  }

  if (step === "blocked") {
    return (
      <>
        <h1>Cannot enrol</h1>
        <div className="notice error">
          <p>{blockedReason}</p>
        </div>
        <p className="footnote">
          You can always enrol at the doorboard itself. That path uses the door&apos;s own camera and
          sends nothing over the internet.
        </p>
      </>
    );
  }

  return (
    <>
      <span className="badge">Encrypted on this phone before sending</span>

      <ol className="steps" aria-hidden="true">
        {["consent", "capture", "details", "send"].map((name, index) => (
          <li
            key={name}
            data-state={stepIndex > index ? "done" : stepIndex === index ? "active" : "todo"}
          />
        ))}
      </ol>

      {error ? (
        <div className="notice error" role="alert">
          <p>{error}</p>
        </div>
      ) : null}

      {step === "consent" && doorKey ? (
        <>
          <p className="step-label">Step 1 of 4</p>
          <h1>Please read and agree</h1>
          <p className="lede">
            This is the doorboard&apos;s own consent statement, shown word for word.
          </p>
          <div className="card">
            <div className="consent">{doorKey.consent_text}</div>
            <label className="checkline">
              <input
                type="checkbox"
                checked={consentChecked}
                onChange={(event) => setConsentChecked(event.target.checked)}
              />
              <span>
                I have read this and I agree. I am enrolling my own face
                <span style={{ color: "var(--text-dim)" }}> (version {doorKey.consent_version})</span>
              </span>
            </label>
            <div className="button-row">
              <button className="primary" disabled={!consentChecked} onClick={() => void startCamera()}>
                Continue to photos
              </button>
            </div>
          </div>
        </>
      ) : null}

      {step === "capture" ? (
        <>
          <p className="step-label">Step 2 of 4</p>
          <h1>Take {targetPhotos} photos</h1>
          <p className="lede">
            Good even light, no sunglasses or hat. A few angles help the door recognise you reliably.
          </p>
          <div className="viewfinder">
            <video ref={videoRef} playsInline muted autoPlay />
            <div className="pose">
              {!cameraReady
                ? "Starting the camera…"
                : photos.length >= targetPhotos
                  ? "That's all of them."
                  : (POSES[photos.length] ?? "One more, straight on.")}
            </div>
          </div>
          <div className="thumbs">
            {Array.from({ length: targetPhotos }).map((_unused, index) =>
              previews[index] ? (
                // A plain <img>: these are blob: URLs for local previews, so
                // there is nothing for an image optimiser to fetch or cache.
                <img key={index} src={previews[index]} alt={`Photo ${index + 1}`} />
              ) : (
                <div key={index} className="thumb-empty" />
              ),
            )}
          </div>
          <div className="button-row">
            {photos.length < targetPhotos ? (
              <button className="primary" onClick={() => void capture()} disabled={!cameraReady}>
                {cameraReady
                  ? `Take photo ${photos.length + 1} of ${targetPhotos}`
                  : "Starting the camera…"}
              </button>
            ) : (
              <button className="primary" onClick={() => setStep("details")}>
                Looks good, continue
              </button>
            )}
            {photos.length > 0 ? (
              <button className="secondary" onClick={retakeAll}>
                Start the photos again
              </button>
            ) : null}
          </div>
        </>
      ) : null}

      {step === "details" ? (
        <>
          <p className="step-label">Step 3 of 4</p>
          <h1>How should the door greet you?</h1>
          <div className="card">
            <label htmlFor="display-name">Your name</label>
            <input
              id="display-name"
              type="text"
              value={displayName}
              maxLength={64}
              autoComplete="given-name"
              placeholder="e.g. Tiger"
              onChange={(event) => setDisplayName(event.target.value)}
            />
            <p className="hint">Shown on the doorboard when it recognises you.</p>

            <label htmlFor="colour-choice">Your colour</label>
            <p className="hint">
              Used for your greeting on the screens. Somebody else having the same colour
              is fine — the door tells you apart by name.
            </p>
            <div className="swatches" id="colour-choice">
              {PROFILES.map((entry) => (
                <div key={entry.id}>
                  <button
                    className="swatch"
                    style={{ background: entry.color }}
                    aria-pressed={accentColor.toLowerCase() === entry.color.toLowerCase()}
                    aria-label={entry.name}
                    onClick={() => {
                      // The preset sets both: a nudge toward a distinct door light, and
                      // the colour itself. The picker below can then move the colour
                      // alone, which is the point of separating them.
                      setProfileId(entry.id);
                      setAccentColor(entry.color);
                    }}
                  >
                    {entry.name}
                  </button>
                  <span className="swatch-name">{entry.name}</span>
                </div>
              ))}
            </div>

            <div className="colour-exact">
              <label htmlFor="colour-exact-input">Or pick an exact colour</label>
              <div className="colour-exact__row">
                <input
                  id="colour-exact-input"
                  type="color"
                  value={accentColor}
                  onChange={(event) => setAccentColor(event.target.value)}
                  aria-describedby="colour-exact-hint"
                />
                <input
                  className="colour-exact__hex"
                  type="text"
                  inputMode="text"
                  spellCheck={false}
                  maxLength={7}
                  value={accentColor}
                  aria-label="Colour hex value"
                  onChange={(event) => {
                    // Typed input is accepted as it is written and only committed when it
                    // is a full hex literal, so the swatch does not flicker through
                    // partial values while somebody types "#ff…".
                    const next = event.target.value.trim();
                    if (/^#[0-9a-fA-F]{0,6}$/.test(next)) setAccentColor(next);
                  }}
                />
                <span className="colour-exact__preview" style={{ background: accentColor }} />
              </div>
              <p className="hint" id="colour-exact-hint">
                The door light uses the closest of the six above; the screens use exactly this.
              </p>
            </div>

            <div className="button-row">
              <button className="primary" onClick={() => void submit()}>
                Encrypt and send
              </button>
              <button className="secondary" onClick={() => void startCamera()}>
                Back to photos
              </button>
            </div>
          </div>
        </>
      ) : null}

      {step === "sending" ? (
        <div className="center">
          <div className="spinner" />
          <h1>Encrypting…</h1>
          <p className="lede">Sealing your photos and name so only the door can open them.</p>
        </div>
      ) : null}

      {step === "waiting" ? (
        <div className="center">
          <div className="spinner" />
          <h1>Waiting for the door</h1>
          <p className="lede">
            The door device is collecting your encrypted photos. This usually takes a few seconds.
          </p>
        </div>
      ) : null}

      {step === "done" ? (
        <>
          <h1>{statusReason ? "Not quite" : "You're enrolled"}</h1>
          {statusReason && !statusReason.startsWith("profile_reassigned") ? (
            <div className="notice warn">
              <p>{outcomeMessage(statusReason)}</p>
            </div>
          ) : (
            <div className="notice ok">
              <p>
                The door has your face templates and will greet you by name. Your photos were deleted
                after processing.
              </p>
              {statusReason?.startsWith("profile_reassigned") ? (
                <p style={{ marginBottom: 0 }}>
                  Someone already had the colour you picked, so the door gave you{" "}
                  <strong>{reassignedColourName(statusReason)}</strong> instead — each person needs a
                  different light so the door can tell you apart.
                </p>
              ) : null}
            </div>
          )}
          <p className="footnote">
            You can revoke this at any time by asking the household admin. Revoking deletes your face
            templates immediately.
          </p>
        </>
      ) : null}
    </>
  );
}

async function pollUntilSettled(
  bundleId: string,
  setStep: (step: Step) => void,
  setStatusReason: (reason: string | null) => void,
): Promise<void> {
  const deadline = Date.now() + POLL_TIMEOUT_MS;

  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    try {
      const resp = await fetch(`/api/status/${encodeURIComponent(bundleId)}`, { cache: "no-store" });
      if (!resp.ok) continue;
      const body = (await resp.json()) as { status: string; reason: string | null };

      if (body.status === "enrolled") {
        // A reason on success means something worth telling them (a reassigned
        // colour), not a failure — the done screen decides which box to show.
        setStatusReason(body.reason ?? null);
        setStep("done");
        return;
      }
      if (body.status === "failed" || body.status === "expired") {
        setStatusReason(body.reason ?? body.status);
        setStep("done");
        return;
      }
    } catch {
      // Transient network trouble: keep waiting until the deadline.
    }
  }

  setStatusReason("timed_out");
  setStep("done");
}

/**
 * Relay-side rejection codes, as returned by `/api/enroll/[token]/submit`.
 *
 * Exported for `tests/enrollFlow.test.tsx`, which reads the route's `jsonError`
 * codes off disk and asserts each one lands somewhere useful. That check is only
 * possible if the mapping is reachable from a test.
 */
export function uploadErrorMessage(code: string | undefined): string {
  switch (code) {
    case "invite_already_used":
      return "This invitation has already been used. Ask for a fresh QR code.";
    case "invite_expired":
      return "This invitation expired while you were enrolling. Ask for a fresh QR code.";
    case "invite_not_found":
      return "This link is not valid any more. Ask for a fresh QR code.";
    case "rate_limited":
      return "Too many attempts. Wait a few minutes and try again.";
    case "too_many_images":
      return "That is more photos than this invitation allows. Start the photos again.";
    case "storage_not_configured":
      return "The enrolment service is not fully set up yet. Ask the household admin.";
    case "invalid_bundle":
    case "malformed_json":
    case "invite_mismatch":
      // Only reachable if this page itself sent something wrong, so retrying is
      // pointless; the code is what an admin needs to see.
      return `This page sent something the service could not accept (${code}). Reload and start again, and tell the household admin if it happens twice.`;
    default:
      // The reason is echoed rather than swallowed: an unmapped code used to
      // reach the phone as advice to "please try again", which sent people round
      // the same loop and told whoever they asked for help nothing at all.
      return `The service would not accept the upload (${code ?? "no reason given"}). Please try again.`;
  }
}

/** "profile_reassigned:green_pulse" -> "Green". */
function reassignedColourName(reason: string): string {
  const id = reason.split(":", 2)[1] ?? "";
  return PROFILES.find((entry) => entry.id === id)?.name ?? "another colour";
}

/**
 * Pi-side outcomes, as returned in the pickup ack.
 *
 * Exported for `tests/enrollFlow.test.tsx`, which scrapes every `reason=` the Pi
 * can send out of `door_visiond/{service,enrollment}.py` and asserts none of them
 * falls through to the default. `internal_error` was missing here once and a
 * colour clash reached a real phone as "Ask the household admin to check the
 * doorboard" — true, and useless to both of them.
 */
export function outcomeMessage(reason: string): string {
  switch (reason) {
    case "quality_too_low":
      return "The door could not get a clear enough read of your face. Try again in brighter, even light.";
    case "invite_already_consumed":
    case "unknown_invite":
    case "invite_secret_mismatch":
      return "The door would not accept this invitation. Ask for a fresh QR code.";
    case "invite_revoked":
      return "This invitation was cancelled before the door collected your photos. Ask the household admin for a new one.";
    case "invite_expired":
      return "The invitation expired before the door collected your photos. Ask for a fresh QR code.";
    case "stale_consent":
      return "The consent wording changed while you were enrolling. Please reload and start again.";
    case "display_name_taken":
      return "Someone at this door already uses that name. Start again with a different one — the door needs to tell you apart.";
    case "invalid_accent_color":
      return "That colour wasn't something the door could read. Pick one from the swatches and try again.";
    case "privacy_mode":
      return "Recognition is currently switched off at the door, so enrolment was declined.";
    case "enrollment_storage_locked":
      return "The door's secure storage is locked right now. Ask the household admin, then try again.";
    case "timed_out":
      return "The door did not collect your photos in time. It may be offline — the encrypted copy is deleted automatically. Try again later.";
    case "bundle_expired":
      return "The encrypted copy expired before the door collected it. Try again while the door is online.";
    case "no_profile_available":
      return "Every light colour is already taken by someone enrolled. Ask the household admin to free one up.";
    case "too_many_images":
      return "That was more photos than this invitation allows. Ask for a fresh QR code.";
    case "internal_error":
      return "The door hit an unexpected error saving your enrolment. Nothing was saved — ask the household admin to check the doorboard logs, then try again.";
    default:
      // Same rule as uploadErrorMessage: echo the raw reason so an unmapped one
      // is at least diagnosable from a screenshot.
      return `Enrolment did not complete (${reason}). Nothing was saved. Show this to the household admin.`;
  }
}
