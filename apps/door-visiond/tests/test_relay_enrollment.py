"""Remote enrollment end to end (ADR-0016 P-14, P-16, P-17, P-18, P-19).

These tests drive the real ``VisiondService`` against a fake relay transport, so
the invite rules, the transient-plaintext guarantee, and the degradation
behaviour are all exercised through the same code the Pi runs.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from door_visiond.clock import FakeClock
from door_visiond.relay_client import RelayTransportError, RelayWorker
from door_visiond.service import VisiondService
from door_visiond.settings import Settings
from doorboard_contracts.enrollment_relay import (
    DoorKeyPublication,
    InviteRegistration,
    PickupAck,
    PickupBatch,
    PickupItem,
    SealedBundle,
)

from .conftest import CONSENT_VERSION, capture_logs, scan_tree_for, sentinel
from .relay_helpers import make_manifest, seal_bundle

IMAGE_SENTINEL = sentinel("relayimg")
SECRET_NAME = "ZaphodSentinelName"


class FakeRelayTransport:
    """Records what the Pi sent and hands back whatever bundles a test queued."""

    def __init__(self) -> None:
        self.published: list[DoorKeyPublication] = []
        self.registered: list[InviteRegistration] = []
        self.revoked: list[str] = []
        self.acks: list[PickupAck] = []
        self.queued: list[PickupItem] = []
        self.polls = 0
        self.fail_with: Exception | None = None

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    def publish_door_key(self, publication: DoorKeyPublication) -> None:
        self._maybe_fail()
        self.published.append(publication)

    def register_invite(self, registration: InviteRegistration) -> None:
        self._maybe_fail()
        self.registered.append(registration)

    def revoke_invite(self, invite_id: str) -> None:
        self._maybe_fail()
        self.revoked.append(invite_id)

    def poll_pickup(self) -> PickupBatch:
        self.polls += 1
        self._maybe_fail()
        batch = PickupBatch(items=list(self.queued))
        self.queued.clear()
        return batch

    def acknowledge(self, ack: PickupAck) -> None:
        self._maybe_fail()
        self.acks.append(ack)

    def enqueue(self, bundle: SealedBundle) -> None:
        self.queued.append(PickupItem(bundle=bundle, submitted_at=datetime.now(UTC)))


@pytest.fixture
def relay_settings(ssd_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("VISIOND_RELAY_BASE_URL", "https://enroll.example.test")
    monkeypatch.setenv("VISIOND_RELAY_DEVICE_TOKEN", "device-token")
    monkeypatch.setenv("SSD_DATA_ROOT", str(ssd_settings.ssd_data_root))
    monkeypatch.setenv("VISION_MODE", "mock")
    monkeypatch.setenv("VISIOND_MODEL_DIM", str(ssd_settings.model_dim))
    return Settings()


@pytest.fixture
def transport() -> FakeRelayTransport:
    return FakeRelayTransport()


@pytest.fixture
def svc(relay_settings: Settings, transport: FakeRelayTransport) -> VisiondService:
    return VisiondService(relay_settings, clock=FakeClock(), relay_transport=transport)


def _mint(svc: VisiondService) -> tuple[str, str, str]:
    """Mint an invite and split its URL back into (invite_id, secret, fingerprint).

    The secret and fingerprint now live in the fragment (`#s=<secret>&k=<fp>`, ADR-0043 §2);
    only the invite id is in the path.
    """
    invite = svc.create_invite(label="Tiger's phone")
    url = str(invite["url"])
    parsed = urlparse(url)
    invite_id = parsed.path.rsplit("/", 1)[-1]
    fragment = parse_qs(parsed.fragment)
    secret = fragment["s"][0]
    fingerprint = fragment["k"][0]
    assert invite_id == invite["invite_id"]
    return invite_id, secret, fingerprint


def _bundle(
    svc: VisiondService,
    *,
    invite_id: str,
    secret: str,
    bundle_id: str = "bnd_" + "a" * 22,
    display_name: str = SECRET_NAME,
    images: list[bytes] | None = None,
    consent_version: str = CONSENT_VERSION,
) -> SealedBundle:
    keyring = svc._keyring()
    images = images if images is not None else [IMAGE_SENTINEL + b"-jpeg-payload"]
    return seal_bundle(
        door_public_key=keyring.active_public_key,
        door_key_id=keyring.active_key_id,
        invite_id=invite_id,
        bundle_id=bundle_id,
        manifest=make_manifest(
            invite_secret=secret,
            display_name=display_name,
            consent_version=consent_version,
            image_count=len(images),
        ),
        images=images,
    )


# -- the happy path ---------------------------------------------------------


def test_remote_enrollment_enrolls_and_becomes_matchable(svc: VisiondService) -> None:
    invite_id, secret, fingerprint = _mint(svc)
    ack = svc.relay_handle_bundle(_bundle(svc, invite_id=invite_id, secret=secret))

    assert ack.outcome == "enrolled", ack.reason
    people = svc._store.list_people()
    assert [p["display_name"] for p in people] == [SECRET_NAME]
    # The person is live in the matcher, not merely in the database.
    assert svc.metrics_snapshot()["enrolled"] == 1.0
    assert fingerprint == svc._keyring().fingerprint

    invites = svc.list_invites(include_closed=True)
    assert invites[0]["status"] == "consumed"
    assert invites[0]["person_id"] == people[0]["person_id"]
    # The admin's label stays local and is never part of a relay registration.
    assert invites[0]["label"] == "Tiger's phone"
    for registration in svc._relay_transport.registered:  # type: ignore[union-attr]
        assert "Tiger" not in registration.model_dump_json()


def test_invite_url_carries_the_secret_and_fingerprint_only_in_the_fragment(
    svc: VisiondService,
) -> None:
    """The secret and fingerprint must be in the fragment so neither reaches the relay:
    the fingerprint for E-10, the secret so a compromised relay can't read it from a
    request line (ADR-0043 §2). The path carries only the invite id."""
    invite = svc.create_invite()
    url = str(invite["url"])
    before_fragment, fragment = url.split("#", 1)
    parsed = parse_qs(fragment)
    assert parsed["k"] == [svc._keyring().fingerprint]
    assert parsed["s"][0]  # the secret is present in the fragment
    # Neither secret nor fingerprint appears before the '#', where a server would see it.
    assert svc._keyring().fingerprint not in before_fragment
    assert parsed["s"][0] not in before_fragment


# -- P-14: invite single use, expiry, revocation, forgery -------------------


def test_invite_cannot_be_used_twice(svc: VisiondService) -> None:
    invite_id, secret, _ = _mint(svc)
    first = svc.relay_handle_bundle(_bundle(svc, invite_id=invite_id, secret=secret))
    second = svc.relay_handle_bundle(
        _bundle(svc, invite_id=invite_id, secret=secret, bundle_id="bnd_" + "c" * 22)
    )

    assert first.outcome == "enrolled"
    assert second.outcome == "rejected"
    assert second.reason == "invite_already_consumed"
    assert svc._store.person_count() == 1


def test_concurrent_pickups_of_one_invite_enroll_exactly_once(svc: VisiondService) -> None:
    """Two workers racing the same invite must not produce two people (E-11)."""
    invite_id, secret, _ = _mint(svc)
    bundles = [
        _bundle(svc, invite_id=invite_id, secret=secret, bundle_id=f"bnd_{'d' * 21}{i}")
        for i in range(2)
    ]

    async def _race() -> list[PickupAck]:
        return list(
            await asyncio.gather(*(asyncio.to_thread(svc.relay_handle_bundle, b) for b in bundles))
        )

    acks = asyncio.run(_race())
    outcomes = sorted(ack.outcome for ack in acks)
    assert outcomes == ["enrolled", "rejected"]
    assert svc._store.person_count() == 1


def test_expired_invite_is_rejected(svc: VisiondService) -> None:
    invite_id, secret = svc._store.create_invite(
        expires_at=datetime.now(UTC) - timedelta(seconds=1), max_images=5
    )
    ack = svc.relay_handle_bundle(_bundle(svc, invite_id=invite_id, secret=secret))
    assert (ack.outcome, ack.reason) == ("rejected", "invite_expired")
    assert svc._store.person_count() == 0


def test_revoked_invite_is_rejected(svc: VisiondService) -> None:
    invite_id, secret, _ = _mint(svc)
    assert svc.revoke_invite(invite_id) == {"revoked": True}
    ack = svc.relay_handle_bundle(_bundle(svc, invite_id=invite_id, secret=secret))
    assert (ack.outcome, ack.reason) == ("rejected", "invite_revoked")
    assert svc._store.person_count() == 0
    assert svc._relay_transport.revoked == [invite_id]  # type: ignore[union-attr]


def test_invite_the_pi_never_issued_is_rejected(svc: VisiondService) -> None:
    """A compromised relay cannot invent an invite: the Pi's table is authoritative."""
    svc._keyring()  # ensure a keyring exists to seal against
    ack = svc.relay_handle_bundle(
        _bundle(svc, invite_id="inv_" + "f" * 22, secret="Zm9yZ2VkLXNlY3JldA")
    )
    assert (ack.outcome, ack.reason) == ("rejected", "unknown_invite")
    assert svc._store.person_count() == 0


def test_wrong_invite_secret_is_rejected(svc: VisiondService) -> None:
    """The relay only stores sha256(secret), so it cannot seal a valid manifest."""
    invite_id, _secret, _ = _mint(svc)
    ack = svc.relay_handle_bundle(
        _bundle(svc, invite_id=invite_id, secret="d3Jvbmctc2VjcmV0LXZhbHVl")
    )
    assert (ack.outcome, ack.reason) == ("rejected", "invite_secret_mismatch")
    assert svc._store.person_count() == 0


def test_more_images_than_the_invite_allows_is_rejected(svc: VisiondService) -> None:
    invite_id, secret = svc._store.create_invite(
        expires_at=datetime.now(UTC) + timedelta(hours=1), max_images=1
    )
    ack = svc.relay_handle_bundle(
        _bundle(svc, invite_id=invite_id, secret=secret, images=[b"one-image", b"two-image"])
    )
    assert (ack.outcome, ack.reason) == ("rejected", "too_many_images")


def test_stale_consent_version_is_rejected(svc: VisiondService) -> None:
    invite_id, secret, _ = _mint(svc)
    ack = svc.relay_handle_bundle(
        _bundle(svc, invite_id=invite_id, secret=secret, consent_version="v0")
    )
    assert (ack.outcome, ack.reason) == ("rejected", "stale_consent")
    assert svc._store.person_count() == 0
    # A rejected invite is NOT consumed — the enrollee can retry with current text.
    assert svc.list_invites()[0]["status"] == "open"


# -- P-16: decrypted plaintext is transient --------------------------------


def test_remote_enrollment_leaves_no_plaintext_at_rest(
    svc: VisiondService, relay_settings: Settings
) -> None:
    invite_id, secret, _ = _mint(svc)
    ack = svc.relay_handle_bundle(_bundle(svc, invite_id=invite_id, secret=secret))
    assert ack.outcome == "enrolled"

    hits = scan_tree_for(relay_settings.ssd_data_root, IMAGE_SENTINEL)
    assert hits == [], f"decrypted image bytes survived at {hits}"
    # The raw image never touches disk now (embedded from memory), so the tmp root is empty —
    # or absent entirely, which is just as good. Either way, no plaintext image is left behind.
    tmp_root = relay_settings.enroll_tmp_root
    assert not (tmp_root.exists() and any(tmp_root.iterdir())), "raw images left in the tmp root"


def test_failed_remote_enrollment_still_wipes_plaintext(
    svc: VisiondService, relay_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    invite_id, secret, _ = _mint(svc)
    bundle = _bundle(svc, invite_id=invite_id, secret=secret)

    def _explode(_image: bytes) -> tuple[object, float]:
        msg = "injected embedder failure"
        raise RuntimeError(msg)

    monkeypatch.setattr(svc._embedder, "embed", _explode)
    ack = svc.relay_handle_bundle(bundle)

    assert (ack.outcome, ack.reason) == ("failed", "internal_error")
    hits = scan_tree_for(relay_settings.ssd_data_root, IMAGE_SENTINEL)
    assert hits == [], f"plaintext survived a failed enrollment at {hits}"
    # The raw image never touches disk now (embedded from memory), so the tmp root is empty —
    # or absent entirely, which is just as good. Either way, no plaintext image is left behind.
    tmp_root = relay_settings.enroll_tmp_root
    assert not (tmp_root.exists() and any(tmp_root.iterdir())), "raw images left in the tmp root"


# -- P-19: the relay path logs nothing sensitive ---------------------------


def test_relay_path_logs_are_clean(svc: VisiondService) -> None:
    invite_id, secret, _ = _mint(svc)
    bundle = _bundle(svc, invite_id=invite_id, secret=secret)

    with capture_logs("door_visiond") as records:
        assert svc.relay_handle_bundle(bundle).outcome == "enrolled"

    blob = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in records)
    assert IMAGE_SENTINEL.decode() not in blob
    assert SECRET_NAME not in blob, "a display name reached the logs"
    assert secret not in blob, "an invite secret reached the logs"
    assert records, "expected the relay path to log something"


def test_minting_an_invite_never_logs_the_secret(svc: VisiondService) -> None:
    with capture_logs("door_visiond") as records:
        invite = svc.create_invite(label="Tiger's phone")
    secret = parse_qs(urlparse(str(invite["url"])).fragment)["s"][0]
    blob = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in records)
    assert secret not in blob


# -- P-18: privacy mode and storage lock stop collection -------------------


def test_privacy_mode_blocks_collection(svc: VisiondService) -> None:
    svc.set_privacy_mode(enabled=True, changed_by="admin")
    assert svc.relay_collection_allowed() is False


def test_privacy_mode_refuses_to_mint_invites(svc: VisiondService) -> None:
    from door_visiond.service import PrivacyModeActiveError

    svc.set_privacy_mode(enabled=True, changed_by="admin")
    with pytest.raises(PrivacyModeActiveError):
        svc.create_invite()


def test_privacy_mode_rejects_a_bundle_already_in_flight(svc: VisiondService) -> None:
    invite_id, secret, _ = _mint(svc)
    bundle = _bundle(svc, invite_id=invite_id, secret=secret)
    svc.set_privacy_mode(enabled=True, changed_by="admin")

    ack = svc.relay_handle_bundle(bundle)
    assert (ack.outcome, ack.reason) == ("failed", "privacy_mode")
    assert svc._store.person_count() == 0


def test_locked_enrollment_storage_blocks_collection(
    relay_settings: Settings, transport: FakeRelayTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("door_visiond.service.is_luks_backed", lambda _path: False)
    monkeypatch.setenv("VISIOND_REQUIRE_ENCRYPTED_STORAGE", "true")
    locked = VisiondService(Settings(), clock=FakeClock(), relay_transport=transport)

    assert locked.relay_collection_allowed() is False
    assert locked.health()["enrollment_locked"] is True
    # And no sealing key was written to the unencrypted parent filesystem.
    assert not Settings().relay_key_path.exists()


@pytest.mark.anyio
async def test_worker_does_not_collect_while_privacy_mode_is_on(
    svc: VisiondService, transport: FakeRelayTransport
) -> None:
    invite_id, secret, _ = _mint(svc)
    transport.enqueue(_bundle(svc, invite_id=invite_id, secret=secret))
    svc.set_privacy_mode(enabled=True, changed_by="admin")

    worker = RelayWorker(transport=transport, handler=svc, poll_interval_s=0.01, backoff_max_s=0.05)
    await worker._tick()

    assert transport.polls == 0, "polled for bundles while recognition was disabled"
    assert svc._store.person_count() == 0
    assert transport.acks == []


# -- P-17: a broken relay never blocks the door ----------------------------


@pytest.mark.anyio
async def test_relay_outage_degrades_without_killing_the_loop(
    svc: VisiondService, transport: FakeRelayTransport
) -> None:
    transport.fail_with = RelayTransportError("relay unreachable: ConnectionError")
    worker = RelayWorker(transport=transport, handler=svc, poll_interval_s=0.01, backoff_max_s=0.05)
    await worker.start()
    try:
        for _ in range(200):
            if worker.stats.consecutive_failures >= 3:
                break
            await asyncio.sleep(0.01)
    finally:
        await worker.stop()

    assert worker.stats.consecutive_failures >= 3
    assert worker.stats.degraded is True
    assert worker.stats.last_error == "RelayTransportError"


@pytest.mark.anyio
async def test_relay_outage_leaves_service_health_ok(
    svc: VisiondService, transport: FakeRelayTransport
) -> None:
    """An unreachable relay is not a door fault: /health stays ok (ADR-0016 §6)."""
    transport.fail_with = RelayTransportError("relay unreachable")
    await svc.start()
    try:
        for _ in range(200):
            if svc.relay_status().get("consecutive_failures", 0):
                break
            await asyncio.sleep(0.01)
        health = svc.health()
        assert health["status"] == "ok", health
        assert health["relay_status"] == "degraded"
        assert health["relay_configured"] is True
    finally:
        await svc.stop()


def test_relay_backoff_is_bounded(svc: VisiondService, transport: FakeRelayTransport) -> None:
    worker = RelayWorker(transport=transport, handler=svc, poll_interval_s=1.0, backoff_max_s=7.0)
    delays = [worker._register_failure(RelayTransportError("boom")) for _ in range(20)]
    assert max(delays) <= 7.0
    assert delays[-1] == 7.0


def test_relay_status_reports_disabled_without_configuration(ssd_settings: Settings) -> None:
    plain = VisiondService(ssd_settings, clock=FakeClock())
    assert plain.relay_status() == {"configured": False, "status": "disabled"}
    assert plain.metrics_snapshot()["relay_enabled"] == 0.0


# -- resync: the relay may forget, the Pi must not --------------------------


@pytest.mark.anyio
async def test_worker_republishes_key_and_open_invites_on_resync(
    svc: VisiondService, transport: FakeRelayTransport
) -> None:
    invite_id, _secret, _ = _mint(svc)
    transport.registered.clear()

    worker = RelayWorker(transport=transport, handler=svc, poll_interval_s=0.01, backoff_max_s=0.05)
    await worker._tick()

    assert len(transport.published) == 1
    published = transport.published[0]
    assert published.door_key_id == svc._keyring().active_key_id
    assert published.consent_version == CONSENT_VERSION
    # E-7: the relay serves the Pi's own consent text, not a paraphrase.
    assert published.consent_text.startswith("# Face-recognition consent statement")
    assert [r.invite_id for r in transport.registered] == [invite_id]


@pytest.mark.anyio
async def test_consumed_invites_are_not_republished(
    svc: VisiondService, transport: FakeRelayTransport
) -> None:
    invite_id, secret, _ = _mint(svc)
    assert svc.relay_handle_bundle(_bundle(svc, invite_id=invite_id, secret=secret)).outcome == (
        "enrolled"
    )
    transport.registered.clear()

    worker = RelayWorker(transport=transport, handler=svc, poll_interval_s=0.01, backoff_max_s=0.05)
    await worker._tick()
    assert transport.registered == []


def test_key_rotation_invalidates_outstanding_fingerprints(svc: VisiondService) -> None:
    _invite_id, _secret, old_fingerprint = _mint(svc)
    rotated = svc.rotate_relay_key()
    assert rotated["fingerprint"] != old_fingerprint
    assert svc._keyring().fingerprint == rotated["fingerprint"]


def test_relay_key_path_is_inside_the_enrollment_volume(relay_settings: Settings) -> None:
    assert relay_settings.relay_key_path.is_relative_to(relay_settings.enrollment_root)
    assert Path("/mnt/microsd") not in relay_settings.relay_key_path.parents
