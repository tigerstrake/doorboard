"""Unenrolling someone must take their name off the door now, not in 33 seconds.

`vision.identity_expired` carried only a person_id, so two opposite facts arrived looking
identical:

  * the 2.5 s vision cache lapsed because a face left the frame — routine, constant while
    someone stands at the doorpad looking down at it, and the held name must survive it
    (ADR-0020);
  * the person was unenrolled, or recognition was switched off — their face data is gone and
    their name must come off the screen immediately.

door-visiond knew which was which and dropped it when building the event, so door-api ignored
both. The door told visitors "removal destroys the face data immediately" while continuing to
display the removed person's name for up to 33 s idle, or two minutes mid-interaction.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

# Set before the import: door_api.app builds its state singleton at module scope, so a
# fixture is too late (matching the other HTTP-level suites here).
os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import app, state  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    state.__init__()
    state.startup()
    yield
    state.shutdown()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _recognise() -> None:
    state.machine.handle_identity_stable(
        person_id="prs_alex",
        display_name="Alex",
        profile_id="blue_wave",
        consent_version="v3",
    )
    assert state.identity.current() is not None


def _expire(reason: str | None) -> None:
    """Mirror door-api's handler branch for a vision.identity_expired event."""
    state.machine.handle_identity_expired(person_id="prs_alex")
    if reason in ("admin", "privacy_mode"):
        state.identity.forget_person("prs_alex")


def test_a_face_leaving_the_frame_keeps_the_name(client: TestClient) -> None:
    # The whole reason this holder exists: faces leave frame constantly while someone uses
    # the doorpad, and the greeting must not flicker off each time.
    _recognise()
    _expire("expired")
    held = state.identity.current()
    assert held is not None
    assert held.display_name == "Alex"


def test_an_absent_reason_is_treated_as_the_routine_case(client: TestClient) -> None:
    # An older door sends no reason. Failing toward "keep the name" is right: the deletion
    # paths are the ones that clear explicitly, so the safe default is the routine one.
    _recognise()
    _expire(None)
    assert state.identity.current() is not None


@pytest.mark.parametrize("reason", ["admin", "privacy_mode"])
def test_unenrollment_and_privacy_mode_clear_the_name_immediately(
    client: TestClient, reason: str
) -> None:
    _recognise()
    _expire(reason)
    assert state.identity.current() is None, (
        f"{reason} left the name held; the door promises deletion is immediate"
    )


def test_it_only_forgets_the_person_who_was_removed(client: TestClient) -> None:
    # Someone else being unenrolled must not blank the visitor currently at the door.
    _recognise()
    state.identity.forget_person("prs_someone_else")
    assert state.identity.current() is not None
