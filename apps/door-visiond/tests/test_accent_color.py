"""An accent colour is not an LED profile (ADR-0021).

The bug: `profile.color` was derived from whichever catalogue entry got assigned, and a
taken `profile_id` is reassigned to the next free one — so the second person to ask for
amber was silently given violet on every screen. And with only six catalogue entries, a
seventh person could not enrol at all.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from door_visiond.embedding import Embedding
from door_visiond.enrollment import (
    PROFILE_CATALOG,
    EnrollmentStore,
    InvalidAccentColorError,
    ProfileSpec,
    normalize_accent_color,
)

from .conftest import CONSENT_VERSION, TEST_DIM


def _vector(seed: int) -> Embedding:
    return Embedding(tuple(float((seed + i) % 7) - 3.0 for i in range(TEST_DIM)))


def _enroll(store: EnrollmentStore, name: str, profile: ProfileSpec, seed: int = 1) -> str:
    return store.enroll(
        display_name=name,
        consent_version=CONSENT_VERSION,
        consent_at=datetime(2026, 8, 17, tzinfo=UTC),
        embeddings=[(_vector(seed), "mock-embedder-v1", 0.9)],
        profile=profile,
    )


def _person(store: EnrollmentStore, person_id: str) -> dict[str, object]:
    return next(p for p in store.list_people() if p["person_id"] == person_id)


@pytest.fixture
def store(tmp_path: Path) -> EnrollmentStore:
    return EnrollmentStore(tmp_path / "enrollment.sqlite")


class TestChosenColourSurvives:
    def test_a_reassigned_profile_keeps_the_chosen_colour(self, store: EnrollmentStore) -> None:
        """The actual reported bug: pick amber second, get amber."""
        _enroll(store, "First", ProfileSpec("warm_amber", "#ffb300", accent_color="#ffb300"))

        second = _enroll(
            store,
            "Second",
            ProfileSpec("warm_amber", "#ffb300", accent_color="#ffb300"),
            seed=2,
        )

        row = _person(store, second)
        # A different LED effect, because two identical door lights defeat the point...
        assert row["profile_id"] != "warm_amber"
        # ...but the colour they actually chose is untouched.
        assert row["accent_color"] == "#ffb300"

    def test_two_people_may_share_a_colour(self, store: EnrollmentStore) -> None:
        first = _enroll(store, "One", ProfileSpec("blue_wave", "#3a86ff", accent_color="#123456"))
        second = _enroll(
            store, "Two", ProfileSpec("green_pulse", "#3ddc84", accent_color="#123456"), seed=2
        )

        assert _person(store, first)["accent_color"] == "#123456"
        assert _person(store, second)["accent_color"] == "#123456"

    def test_no_preference_falls_back_to_the_catalogue_colour(self, store: EnrollmentStore) -> None:
        """Nothing changes for someone who does not care which colour they get."""
        person = _enroll(store, "Nobody", ProfileSpec("violet_dusk", "#9b5de5"))

        assert _person(store, person)["accent_color"] == "#9b5de5"

    def test_the_matcher_carries_the_chosen_colour(self, store: EnrollmentStore) -> None:
        _enroll(store, "Tiger", ProfileSpec("warm_amber", "#ffb300", accent_color="#00ff99"))

        enrolled = store.load_enrolled()

        assert [p.accent_color for p in enrolled] == ["#00ff99"]


class TestMigration:
    def test_an_existing_database_gains_the_column_and_keeps_its_colours(
        self, tmp_path: Path
    ) -> None:
        """The migration must be invisible on screen the day it runs."""
        db = tmp_path / "old.sqlite"
        legacy = sqlite3.connect(db)
        legacy.executescript(
            """
            CREATE TABLE person (
                person_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                consent_version TEXT NOT NULL, consent_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE profile (
                person_id TEXT PRIMARY KEY REFERENCES person(person_id) ON DELETE CASCADE,
                profile_id TEXT NOT NULL UNIQUE, color TEXT NOT NULL, sound TEXT
            );
            INSERT INTO person VALUES ('prs_old', 'Legacy', 'v3', 'then', 'then');
            INSERT INTO profile VALUES ('prs_old', 'coral_glow', '#ff6b5e', NULL);
            """
        )
        legacy.commit()
        legacy.close()

        migrated = EnrollmentStore(db)

        row = _person(migrated, "prs_old")
        assert row["accent_color"] == "#ff6b5e"

    def test_opening_twice_is_safe(self, tmp_path: Path) -> None:
        """ALTER TABLE is not idempotent, so the guard has to hold on reopen."""
        db = tmp_path / "twice.sqlite"
        EnrollmentStore(db).close()

        reopened = EnrollmentStore(db)

        assert reopened.person_count() == 0


class TestColourValidation:
    @pytest.mark.parametrize("value", ["#fff", "#FFF", "#ffb300", "#FFB300", "  #ffb300  "])
    def test_accepts_hex_literals(self, value: str) -> None:
        assert normalize_accent_color(value) == value.strip().lower()

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_absent_means_no_preference(self, value: str | None) -> None:
        assert normalize_accent_color(value) is None

    @pytest.mark.parametrize(
        "value",
        [
            "red",
            "#12345",
            "#gggggg",
            "rgb(1,2,3)",
            # The reason this is validated at all: the value lands in a CSS custom
            # property, so anything that could close one and open a rule is refused.
            "#fff; background: url(http://evil.test/x)",
            "var(--db-accent)",
            "#fff)",
        ],
    )
    def test_rejects_anything_that_is_not_a_hex_literal(self, value: str) -> None:
        with pytest.raises(InvalidAccentColorError):
            normalize_accent_color(value)


def test_the_catalogue_is_still_the_led_source_of_truth() -> None:
    """Colour is decoupled; the effect ids are still the firmware's, unchanged."""
    assert [entry[0] for entry in PROFILE_CATALOG] == [
        "warm_amber",
        "blue_wave",
        "green_pulse",
        "violet_dusk",
        "coral_glow",
        "cool_white",
    ]
