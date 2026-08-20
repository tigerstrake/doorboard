"""Remote-enrollment relay wire shapes (ADR-0016, mechanism E-13).

These are **API models, not events**.  They live in contracts — unlike the
LAN-only ``/enroll`` shapes, which ADR-0009 §1 keeps visiond-local — because
they cross a trust boundary between two independently deployed artifacts: the
TypeScript relay on Vercel and the Python service on the door Pi.  One source of
truth, two generated languages.

The contract firewall (ADR-0009 E-4) extends here: no model below has a bytes
field, a float-sequence field, or any field able to carry an embedding.  Sealed
payloads are opaque base64url strings that only the door Pi can open, and the
relay never receives a display name — that lives inside the sealed manifest
(ADR-0016 §1).  ``test_enrollment_relay.py`` enforces both properties
structurally, so adding a plaintext-capable field fails CI rather than review.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from doorboard_contracts.events import StrictModel, UTCDateTime

# Base64url without padding — the single representation for every opaque blob
# and identifier that crosses the relay.
type Base64Url = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9_-]+$", min_length=1, max_length=1_400_000),
]

# Opaque relay-visible identifiers: prefix + base62 body, never derived from a
# name (ADR-0005 §8).
type OpaqueId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z]{3,6}_[A-Za-z0-9]{16,32}$"),
]

SEAL_SUITE: str = "ecies-p256-hkdf-sha256-aes256gcm"
"""The one permitted seal suite (ADR-0016 §2). Clients pin it; the Pi rejects others."""

SEAL_INFO_PREFIX: str = "doorboard/enroll-relay/v1"
"""HKDF ``info`` prefix. Domain-separates this use of the door key from any other."""


class SealedItem(StrictModel):
    """One AES-256-GCM item: the manifest (index 0) or a photo (index 1..N).

    ``aad`` is not transmitted — both sides recompute it from
    ``bundle_id || ":" || door_key_id || ":" || index`` so a tampered index or a
    bundle transplanted from another door fails authentication (ADR-0016 §2).
    """

    index: int = Field(ge=0, le=16)
    nonce: Base64Url = Field(description="12-byte GCM nonce, base64url")
    ciphertext: Base64Url = Field(description="GCM ciphertext with appended 16-byte tag")


class SealedBundle(StrictModel):
    """What the phone uploads. Opaque to the relay in its entirety."""

    v: Literal[1] = 1
    suite: Literal["ecies-p256-hkdf-sha256-aes256gcm"] = SEAL_SUITE
    bundle_id: OpaqueId
    invite_id: OpaqueId
    door_key_id: OpaqueId
    ephemeral_public_key: Base64Url = Field(
        description="Uncompressed SEC1 P-256 point of the phone's single-use keypair"
    )
    salt: Base64Url = Field(description="32-byte HKDF salt")
    items: list[SealedItem] = Field(min_length=1, max_length=16)


class SealedProfile(StrictModel):
    """Effects-catalog selection (T-103), carried inside the seal."""

    profile_id: str = Field(min_length=1, max_length=64)
    color: str = Field(min_length=1, max_length=32)
    sound: str | None = Field(default=None, max_length=64)
    # The colour the enrollee chose for the screens (ADR-0021), separate from `color`,
    # which is the catalogue colour of the LED effect and moves if that effect is
    # reassigned. Optional so a phone running older relay code still enrolls.
    accent_color: str | None = Field(default=None, max_length=32)


class SealedManifest(StrictModel):
    """Item 0 of a sealed bundle: **plaintext, and never relay-visible.**

    This is the one shape in this module that carries nominal data, which is
    precisely why it lives inside the AEAD envelope rather than beside it.  It is
    deliberately excluded from ``RELAY_MODELS`` and from the E-13 field audit —
    see ``test_enrollment_relay.py``, which asserts that exclusion holds.

    ``invite_secret`` travels here rather than as a relay field so that a
    compromised relay — which stores only ``sha256(secret)`` — cannot construct a
    bundle the Pi will accept, even though sealing needs only the public key
    (ADR-0016 §4, E-11).
    """

    invite_secret: Base64Url
    display_name: str = Field(min_length=1, max_length=64)
    consent_version: str = Field(min_length=1, max_length=16)
    consent_confirmed: Literal[True]
    profile: SealedProfile
    captured_at: UTCDateTime
    image_count: int = Field(ge=1, le=15)


class DoorKeyPublication(StrictModel):
    """Pushed by the Pi (outbound) so phones can seal to it.

    Carries the consent statement because ADR-0009 E-7 makes the Pi's copy the
    single source enrollees must see verbatim; the relay only relays it.
    """

    door_key_id: OpaqueId
    suite: Literal["ecies-p256-hkdf-sha256-aes256gcm"] = SEAL_SUITE
    public_key: Base64Url = Field(description="Uncompressed SEC1 P-256 point")
    fingerprint: Base64Url = Field(
        description="First 16 bytes of SHA-256 over the public key, base64url. "
        "The QR fragment carries this so a substituted key is detectable (E-10)."
    )
    consent_version: str = Field(min_length=1, max_length=16)
    consent_text: str = Field(min_length=1, max_length=32_000)
    published_at: UTCDateTime


class InviteRegistration(StrictModel):
    """Pushed by the Pi when an admin mints an invite (ADR-0016 §4 step 1).

    Only the *hash* of the secret travels, so the relay cannot reconstruct a
    working invite URL and cannot mint one the Pi will honour (E-11).
    """

    invite_id: OpaqueId
    secret_sha256: Base64Url
    expires_at: UTCDateTime
    max_images: int = Field(default=5, ge=1, le=16)


class InvitePublicState(StrictModel):
    """What the phone may learn about its own invite before sealing."""

    invite_id: OpaqueId
    status: Literal["open", "consumed", "expired", "revoked", "unknown"]
    max_images: int = Field(ge=1, le=16)
    expires_at: UTCDateTime | None = None


class BundleSubmitAccepted(StrictModel):
    bundle_id: OpaqueId
    status: Literal["pending"] = "pending"
    expires_at: UTCDateTime


class BundleStatus(StrictModel):
    """Progress for the phone to poll. No name, no biometric data (ADR-0016 §5)."""

    bundle_id: OpaqueId
    status: Literal["pending", "collected", "enrolled", "failed", "expired"]
    reason: str | None = Field(
        default=None,
        max_length=200,
        description="Machine-readable failure cause; never contains user data",
    )
    updated_at: UTCDateTime


class PickupItem(StrictModel):
    """One pending bundle handed to the Pi on its outbound poll."""

    bundle: SealedBundle
    submitted_at: UTCDateTime


class PickupBatch(StrictModel):
    items: list[PickupItem] = Field(default_factory=list[PickupItem], max_length=8)


class PickupAck(StrictModel):
    """The Pi's verdict. Deletes the ciphertext and updates the phone's status."""

    bundle_id: OpaqueId
    outcome: Literal["enrolled", "failed", "rejected"]
    reason: str | None = Field(
        default=None,
        max_length=200,
        description="Machine-readable only — never a display name or quality vector",
    )


class RelayHealth(StrictModel):
    status: Literal["ok", "degraded"]
    pending_bundles: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Visitor surface (ADR-0017)
#
# Unlike the enrollment shapes above, these carry plaintext — a guestbook note
# exists in order to be shown on a hallway wallboard, so encrypting it would be
# theatre (ADR-0017 §"The data is public by design").  What keeps this safe is
# scope, not secrecy: the snapshot below is an *allow-list* projection of public
# session state, and `test_enrollment_relay.py` enforces that no identity, media,
# or diagnostic field creeps into it (E-15).
# ---------------------------------------------------------------------------


class VisitorPollOption(StrictModel):
    option_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)


class VisitorPoll(StrictModel):
    poll_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=280)
    options: list[VisitorPollOption] = Field(min_length=1, max_length=8)


class VisitorPollResult(StrictModel):
    option_id: str = Field(min_length=1, max_length=64)
    votes: int = Field(ge=0)


class VisitorActionOutcome(StrictModel):
    """Echoes back what became of one queued action, so the phone can confirm."""

    action_id: OpaqueId
    kind: Literal["note", "vote", "deletion_request"]
    status: Literal["applied", "rejected"]
    reason: str | None = Field(default=None, max_length=200)
    entry_id: str | None = Field(default=None, max_length=64)


class VisitorSessionSnapshot(StrictModel):
    """What door-api pushes and a phone may read (ADR-0017 §2 — binding allow-list).

    Deliberately *not* derived from the session machine's own object: every field
    is named here, so adding one is a visible, reviewable act rather than a
    side effect of a refactor upstream.
    """

    session_token_sha256: Base64Url
    session_id: OpaqueId
    state: str = Field(min_length=1, max_length=32)
    expires_at: UTCDateTime
    poll: VisitorPoll | None = None
    poll_results: list[VisitorPollResult] | None = None
    outcomes: list[VisitorActionOutcome] = Field(
        default_factory=list[VisitorActionOutcome], max_length=16
    )
    # Display name of the recognised person, when their consent covers attribution
    # (ADR-0018 §2). Present so the page can disclose whose name will be attached
    # before they write; None for an unrecognised visitor.
    attributed_to: str | None = Field(default=None, max_length=64)
    pushed_at: UTCDateTime


class VisitorPublicSnapshot(StrictModel):
    """The snapshot minus the token hash — what actually reaches the phone.

    The hash authorises the request; there is no reason to hand it back, and not
    returning it keeps it out of browser history, screenshots, and logs.
    """

    session_id: OpaqueId
    state: str = Field(min_length=1, max_length=32)
    expires_at: UTCDateTime
    poll: VisitorPoll | None = None
    poll_results: list[VisitorPollResult] | None = None
    outcomes: list[VisitorActionOutcome] = Field(
        default_factory=list[VisitorActionOutcome], max_length=16
    )
    attributed_to: str | None = Field(default=None, max_length=64)
    pushed_at: UTCDateTime


class VisitorNoteAction(StrictModel):
    kind: Literal["note"] = "note"
    text: str = Field(min_length=1, max_length=500)


class VisitorVoteAction(StrictModel):
    kind: Literal["vote"] = "vote"
    poll_id: str = Field(min_length=1, max_length=64)
    option_id: str = Field(min_length=1, max_length=64)


class VisitorDeletionAction(StrictModel):
    kind: Literal["deletion_request"] = "deletion_request"
    target_kind: Literal["guestbook", "checkin", "photo", "video_message"]
    target_id: str = Field(min_length=1, max_length=64)


class VisitorQueuedAction(StrictModel):
    """One visitor write, waiting for door-api to collect it."""

    action_id: OpaqueId
    session_id: OpaqueId
    submitted_at: UTCDateTime
    note: VisitorNoteAction | None = None
    vote: VisitorVoteAction | None = None
    deletion_request: VisitorDeletionAction | None = None


class VisitorActionBatch(StrictModel):
    items: list[VisitorQueuedAction] = Field(
        default_factory=list[VisitorQueuedAction], max_length=16
    )


class VisitorActionAck(StrictModel):
    """door-api reporting what it did with a collected action."""

    outcomes: list[VisitorActionOutcome] = Field(min_length=1, max_length=16)


class VisitorActionAccepted(StrictModel):
    action_id: OpaqueId
    status: Literal["queued"] = "queued"


SEALED_PLAINTEXT_MODELS: tuple[type[StrictModel], ...] = (
    SealedProfile,
    SealedManifest,
)
"""Shapes that exist only *inside* the AEAD envelope. Never a relay request or
response body — the relay cannot represent them, let alone read them."""


RELAY_MODELS: tuple[type[StrictModel], ...] = (
    SealedItem,
    SealedBundle,
    DoorKeyPublication,
    InviteRegistration,
    InvitePublicState,
    BundleSubmitAccepted,
    BundleStatus,
    PickupItem,
    PickupBatch,
    PickupAck,
    RelayHealth,
    VisitorPollOption,
    VisitorPoll,
    VisitorPollResult,
    VisitorActionOutcome,
    VisitorSessionSnapshot,
    VisitorPublicSnapshot,
    VisitorNoteAction,
    VisitorVoteAction,
    VisitorDeletionAction,
    VisitorQueuedAction,
    VisitorActionBatch,
    VisitorActionAck,
    VisitorActionAccepted,
)

VISITOR_SNAPSHOT_FIELDS: frozenset[str] = frozenset(VisitorSessionSnapshot.model_fields)
"""ADR-0017 §2's allow-list, as data. The door-api projection test asserts against
this so the binding list and the code cannot drift."""
"""Rendered to TypeScript and JSON Schema by ``contracts generate-ts`` / ``export-schemas``."""
