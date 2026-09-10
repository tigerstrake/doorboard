"""Unit tests for door_api.social — the business logic layer, no HTTP.

Coverage targets from the T-403 brief:
- Injection corpus (script tags, markdown, emoji floods) never crashes and
  is provably inert once escaped at render time.
- Rate limits enforced per-IP and per-session-token.
- Vote-once-per-session-token enforcement.
- Deletion request propagation + audit log.
- Malformed-but-well-formed input is rejected, never crashes a loop.
"""

from __future__ import annotations

import json
import time
import unicodedata
from datetime import date
from typing import Any

import pytest
from door_api.social.config import SocialConfig
from door_api.social.errors import (
    AlreadyVotedError,
    NotFoundError,
    PollClosedError,
    RateLimitedError,
    UnsupportedDeletionTargetError,
)
from door_api.social.sanitize import SanitizationError, escape_for_render, sanitize_text
from door_api.social.service import SocialService, compute_day_streaks
from door_api.social.store import SocialStore
from doorboard_contracts.events import parse_event

INJECTION_CORPUS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "'; DROP TABLE guestbook_entries; --",
    "**markdown** _injection_ [link](javascript:alert(1))",
    "hello\x00\x01\x07world",
    "🎉" * 100,
    "a" * 10_000,
]


def make_service(**config_overrides: Any) -> SocialService:
    config = SocialConfig(db_path=":memory:", **config_overrides)
    store = SocialStore(config.db_path)
    events: list[dict[str, Any]] = []
    service = SocialService(config=config, store=store, on_event=events.append)
    service.events = events  # type: ignore[attr-defined]
    return service


def test_emitted_social_event_uses_contract_envelope() -> None:
    service = make_service()
    service.create_guestbook_entry(
        text="hello",
        author_label="visitor",
        ip="10.0.0.1",
        session_token="session",
        trace_id="request-trace",
    )

    event = parse_event(service.events[-1])  # type: ignore[attr-defined]
    assert event.type == "social.guestbook_entry_created"
    assert event.source == "door-api"
    assert event.door_id == "primary"


# ---------------------------------------------------------------------------
# Injection corpus
# ---------------------------------------------------------------------------


class TestInjectionCorpus:
    @pytest.mark.parametrize("hostile", INJECTION_CORPUS)
    def test_guestbook_create_never_crashes(self, hostile: str) -> None:
        service = make_service(guestbook_text_max_len=20_000)
        try:
            entry = service.create_guestbook_entry(
                text=hostile, author_label=None, ip="10.0.0.1", session_token="s1", trace_id="t1"
            )
        except SanitizationError:
            return  # over length cap or empty after control-char strip — acceptable rejection
        # Stored raw (not HTML-escaped) — escaping happens only at render.
        assert "&lt;" not in entry.text or "&lt;" in hostile
        # But rendering it is always inert once escaped.
        rendered = escape_for_render(entry.text)
        assert "<script>" not in rendered
        assert "<img" not in rendered

    def test_control_characters_stripped(self) -> None:
        clean = sanitize_text("hello\x00\x07world", max_len=100)
        assert clean == "helloworld"

    def test_script_tag_escaped_for_render(self) -> None:
        rendered = escape_for_render("<script>alert(1)</script>")
        assert rendered == "&lt;script&gt;alert(1)&lt;/script&gt;"

    def test_empty_after_strip_rejected(self) -> None:
        service = make_service()
        with pytest.raises(SanitizationError):
            service.create_guestbook_entry(
                text="   ", author_label=None, ip="10.0.0.1", session_token="s1", trace_id="t1"
            )

    def test_over_length_rejected_not_crashed(self) -> None:
        service = make_service(guestbook_text_max_len=10)
        with pytest.raises(SanitizationError):
            service.create_guestbook_entry(
                text="a" * 500, author_label=None, ip="10.0.0.1", session_token="s1", trace_id="t1"
            )
        assert service.metrics.guestbook_rejected_invalid == 1
        assert service.metrics.guestbook_created == 0


# ---------------------------------------------------------------------------
# Unicode: emoji survive, controls go, zalgo is clamped (audit 2026-09, P3)
# ---------------------------------------------------------------------------


def _combining_runs(text: str) -> list[int]:
    """Lengths of every run of consecutive non-spacing marks in *text*."""
    runs: list[int] = []
    run = 0
    for ch in text:
        if unicodedata.category(ch) == "Mn" and ch != "\ufe0f":
            run += 1
        else:
            if run:
                runs.append(run)
            run = 0
    if run:
        runs.append(run)
    return runs


class TestUnicodeSanitisation:
    def test_family_emoji_round_trips(self) -> None:
        """U+200D ZWJ is Cf and used to be stripped, turning one family into three
        separate people on the wallboard."""
        family = "👨\u200d👩\u200d👧\u200d👦"
        assert sanitize_text(family, max_len=100) == family

    def test_profession_and_flag_style_sequences_round_trip(self) -> None:
        for seq in ("👩\u200d💻", "🏳\ufe0f\u200d🌈", "👍🏽"):
            assert sanitize_text(seq, max_len=100) == seq

    def test_variation_selector_and_keycap_survive(self) -> None:
        text = "I ❤\ufe0f 1\ufe0f\u20e3"
        assert sanitize_text(text, max_len=100) == text

    def test_bidi_override_is_still_stripped(self) -> None:
        # RLO, LRE, FSI/PDI: all Cf, all gone, text order preserved.
        assert sanitize_text("abc\u202edef\u202a\u2068x\u2069", max_len=100) == "abcdefx"

    def test_zero_width_space_is_still_stripped(self) -> None:
        assert sanitize_text("a\u200bb\ufeffc", max_len=100) == "abc"

    def test_control_characters_are_still_stripped(self) -> None:
        assert sanitize_text("hi\x00\x1b[31mthere\r\n", max_len=100) == "hi[31mthere"

    def test_decomposed_input_is_nfc_normalised(self) -> None:
        assert sanitize_text("cafe\u0301", max_len=100) == "café"
        # And the cap sees the composed form: one precomposed é plus one mark, not three.
        assert sanitize_text("e\u0301\u0302", max_len=100) == "é\u0302"

    def test_zalgo_is_clamped_to_three_marks_per_base(self) -> None:
        marks = "\u0300\u0301\u0302\u0303\u0304\u0305\u0306\u0307"
        zalgo = "x" + marks * 5 + "q" + marks * 5 + "z"
        clean = sanitize_text(zalgo, max_len=1000)
        assert clean == "x\u0300\u0301\u0302q\u0300\u0301\u0302z"
        assert max(_combining_runs(clean)) <= 3

    def test_interleaved_glue_does_not_defeat_the_clamp(self) -> None:
        zalgo = "x" + "\u0301\ufe0f" * 20 + "y"
        clean = sanitize_text(zalgo, max_len=1000)
        assert max(_combining_runs(clean)) <= 3
        assert clean.startswith("x") and clean.endswith("y")

    def test_real_orthographies_are_untouched(self) -> None:
        for text in ("naïve façade", "Việt Nam", "हिन्दी", "العربية", "ไทย", "🇳🇿 kia ora"):
            assert sanitize_text(text, max_len=100) == unicodedata.normalize("NFC", text)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def test_per_ip_limit_enforced(self) -> None:
        service = make_service(write_rate_limit_count=2, write_rate_limit_window_s=60.0)
        clock = [0.0]
        service.set_now_fn(lambda: clock[0])

        for i in range(2):
            service.create_guestbook_entry(
                text=f"entry {i}",
                author_label=None,
                ip="10.0.0.1",
                session_token=f"sess-{i}",
                trace_id="t",
            )
        with pytest.raises(RateLimitedError):
            service.create_guestbook_entry(
                text="entry 3",
                author_label=None,
                ip="10.0.0.1",
                session_token="sess-3",
                trace_id="t",
            )
        assert service.metrics.guestbook_rejected_rate_limited == 1

    def test_per_session_limit_enforced_even_with_rotating_ip(self) -> None:
        service = make_service(write_rate_limit_count=2, write_rate_limit_window_s=60.0)
        clock = [0.0]
        service.set_now_fn(lambda: clock[0])

        for i in range(2):
            service.create_guestbook_entry(
                text=f"entry {i}",
                author_label=None,
                ip=f"10.0.0.{i}",
                session_token="same-session",
                trace_id="t",
            )
        # Rotating the IP does not bypass the per-session-token limit.
        with pytest.raises(RateLimitedError):
            service.create_guestbook_entry(
                text="entry 3",
                author_label=None,
                ip="10.0.0.99",
                session_token="same-session",
                trace_id="t",
            )

    def test_limit_resets_after_window_slides(self) -> None:
        service = make_service(write_rate_limit_count=1, write_rate_limit_window_s=60.0)
        clock = [0.0]
        service.set_now_fn(lambda: clock[0])

        service.create_guestbook_entry(
            text="first", author_label=None, ip="10.0.0.1", session_token="s1", trace_id="t"
        )
        with pytest.raises(RateLimitedError):
            service.create_guestbook_entry(
                text="second", author_label=None, ip="10.0.0.1", session_token="s2", trace_id="t"
            )
        clock[0] = 61.0
        # Should succeed now that the window has slid past the first hit.
        service.create_guestbook_entry(
            text="third", author_label=None, ip="10.0.0.1", session_token="s3", trace_id="t"
        )

    def test_rate_limit_applies_to_poll_votes_and_checkins(self) -> None:
        service = make_service(write_rate_limit_count=1, write_rate_limit_window_s=60.0)
        clock = [0.0]
        service.set_now_fn(lambda: clock[0])
        poll = service.create_poll(question="Snack?", options=["Tea", "Coffee"])

        service.create_checkin(
            person_id=None, label="guest", ip="10.0.0.1", session_token="s1", trace_id="t"
        )
        with pytest.raises(RateLimitedError):
            service.cast_vote(
                poll_id=poll.id,
                option_id=poll.options[0].id,
                ip="10.0.0.1",
                session_token="s2",
                trace_id="t",
            )


# ---------------------------------------------------------------------------
# Poll vote-once enforcement
# ---------------------------------------------------------------------------


class TestPollVoting:
    def test_one_vote_per_session_token(self) -> None:
        service = make_service()
        poll = service.create_poll(question="Snack?", options=["Tea", "Coffee"])

        service.cast_vote(
            poll_id=poll.id,
            option_id=poll.options[0].id,
            ip="10.0.0.1",
            session_token="visitor-1",
            trace_id="t",
        )
        with pytest.raises(AlreadyVotedError):
            service.cast_vote(
                poll_id=poll.id,
                option_id=poll.options[1].id,
                ip="10.0.0.2",
                session_token="visitor-1",
                trace_id="t",
            )

        results = service.poll_results(poll.id)
        tea = next(r for r in results if r["option_id"] == poll.options[0].id)
        assert tea["votes"] == 1

    def test_different_session_tokens_can_both_vote(self) -> None:
        service = make_service()
        poll = service.create_poll(question="Snack?", options=["Tea", "Coffee"])

        service.cast_vote(
            poll_id=poll.id,
            option_id=poll.options[0].id,
            ip="10.0.0.1",
            session_token="visitor-1",
            trace_id="t",
        )
        service.cast_vote(
            poll_id=poll.id,
            option_id=poll.options[0].id,
            ip="10.0.0.2",
            session_token="visitor-2",
            trace_id="t",
        )
        results = service.poll_results(poll.id)
        tea = next(r for r in results if r["option_id"] == poll.options[0].id)
        assert tea["votes"] == 2

    def test_vote_against_unknown_poll_rejected(self) -> None:
        service = make_service()
        with pytest.raises(NotFoundError):
            service.cast_vote(
                poll_id="nonexistent",
                option_id="x",
                ip="10.0.0.1",
                session_token="v1",
                trace_id="t",
            )

    def test_vote_against_unknown_option_rejected(self) -> None:
        service = make_service()
        poll = service.create_poll(question="Snack?", options=["Tea", "Coffee"])
        with pytest.raises(NotFoundError):
            service.cast_vote(
                poll_id=poll.id,
                option_id="not-a-real-option",
                ip="10.0.0.1",
                session_token="v1",
                trace_id="t",
            )

    def test_vote_against_closed_poll_rejected(self) -> None:
        service = make_service()
        poll = service.create_poll(question="Snack?", options=["Tea", "Coffee"])
        service.close_poll(poll.id)
        with pytest.raises(PollClosedError):
            service.cast_vote(
                poll_id=poll.id,
                option_id=poll.options[0].id,
                ip="10.0.0.1",
                session_token="v1",
                trace_id="t",
            )

    def test_create_poll_needs_at_least_two_options(self) -> None:
        service = make_service()
        with pytest.raises(SanitizationError):
            service.create_poll(question="Snack?", options=["Tea"])

    def test_poll_events_emitted(self) -> None:
        service = make_service()
        poll = service.create_poll(question="Snack?", options=["Tea", "Coffee"])
        service.cast_vote(
            poll_id=poll.id,
            option_id=poll.options[0].id,
            ip="10.0.0.1",
            session_token="v1",
            trace_id="t",
        )
        emitted_types = [e["type"] for e in service.events]  # type: ignore[attr-defined]
        assert "social.poll_vote_cast" in emitted_types


# ---------------------------------------------------------------------------
# Deletion propagation + moderation audit log
# ---------------------------------------------------------------------------


class TestDeletion:
    def test_deletion_request_removes_from_public_list(self) -> None:
        service = make_service()
        entry = service.create_guestbook_entry(
            text="hello", author_label=None, ip="10.0.0.1", session_token="s1", trace_id="t"
        )
        service.approve_guestbook_entry(entry.id)
        assert len(service.list_public_guestbook_entries(limit=10, cursor=None)) == 1

        service.request_deletion(
            target_kind="guestbook",
            target_id=entry.id,
            ip="10.0.0.1",
            session_token="s1",
            trace_id="t",
        )
        assert service.list_public_guestbook_entries(limit=10, cursor=None) == []

    def test_deletion_emits_event_and_audit_log(self) -> None:
        service = make_service()
        entry = service.create_guestbook_entry(
            text="hello", author_label=None, ip="10.0.0.1", session_token="s1", trace_id="t"
        )
        service.request_deletion(
            target_kind="guestbook",
            target_id=entry.id,
            ip="10.0.0.1",
            session_token="s1",
            trace_id="t",
        )
        emitted_types = [e["type"] for e in service.events]  # type: ignore[attr-defined]
        assert "social.deletion_requested" in emitted_types

        log = service.moderation_log(limit=10)
        actions = [(e["action"], e["actor"]) for e in log if e["target_id"] == entry.id]
        assert ("created", "visitor") in actions
        assert ("deleted", "visitor") in actions

    def test_checkin_deletion_removes_from_list(self) -> None:
        service = make_service()
        checkin = service.create_checkin(
            person_id="prs_1", label="Alex", ip="10.0.0.1", session_token="s1", trace_id="t"
        )
        assert len(service.list_checkins(limit=10, cursor=None)) == 1
        service.request_deletion(
            target_kind="checkin",
            target_id=checkin.id,
            ip="10.0.0.1",
            session_token="s1",
            trace_id="t",
        )
        assert service.list_checkins(limit=10, cursor=None) == []

    def test_unsupported_deletion_target_rejected(self) -> None:
        service = make_service()
        with pytest.raises(UnsupportedDeletionTargetError):
            service.request_deletion(
                target_kind="video_message",
                target_id="whatever",
                ip="10.0.0.1",
                session_token="s1",
                trace_id="t",
            )

    def test_deleting_unknown_entry_raises_not_found(self) -> None:
        service = make_service()
        with pytest.raises(NotFoundError):
            service.request_deletion(
                target_kind="guestbook",
                target_id="nonexistent",
                ip="10.0.0.1",
                session_token="s1",
                trace_id="t",
            )

    def test_admin_delete_bypasses_visitor_rate_limit(self) -> None:
        service = make_service(write_rate_limit_count=1, write_rate_limit_window_s=60.0)
        clock = [0.0]
        service.set_now_fn(lambda: clock[0])
        e1 = service.create_guestbook_entry(
            text="one", author_label=None, ip="10.0.0.1", session_token="s1", trace_id="t"
        )
        e2 = service.create_guestbook_entry(
            text="two", author_label=None, ip="10.0.0.2", session_token="s2", trace_id="t"
        )
        # Admin can delete both in quick succession without hitting the
        # visitor write-rate-limit.
        service.request_deletion(
            target_kind="guestbook",
            target_id=e1.id,
            ip="10.0.0.9",
            session_token="admin",
            trace_id="t",
            actor="admin",
        )
        service.request_deletion(
            target_kind="guestbook",
            target_id=e2.id,
            ip="10.0.0.9",
            session_token="admin",
            trace_id="t",
            actor="admin",
        )


# ---------------------------------------------------------------------------
# Guestbook moderation workflow
# ---------------------------------------------------------------------------


class TestGuestbookModeration:
    def test_entries_start_pending_and_hidden_from_public(self) -> None:
        service = make_service()
        service.create_guestbook_entry(
            text="hi", author_label=None, ip="10.0.0.1", session_token="s1", trace_id="t"
        )
        assert service.list_public_guestbook_entries(limit=10, cursor=None) == []
        pending = service.list_admin_guestbook_entries(status="pending", limit=10, cursor=None)
        assert len(pending) == 1

    def test_approve_moves_entry_to_public(self) -> None:
        service = make_service()
        entry = service.create_guestbook_entry(
            text="hi", author_label=None, ip="10.0.0.1", session_token="s1", trace_id="t"
        )
        service.approve_guestbook_entry(entry.id)
        assert len(service.list_public_guestbook_entries(limit=10, cursor=None)) == 1
        assert service.list_admin_guestbook_entries(status="pending", limit=10, cursor=None) == []

    def test_approving_unknown_entry_raises(self) -> None:
        service = make_service()
        with pytest.raises(NotFoundError):
            service.approve_guestbook_entry("nonexistent")

    def test_approving_twice_raises(self) -> None:
        service = make_service()
        entry = service.create_guestbook_entry(
            text="hi", author_label=None, ip="10.0.0.1", session_token="s1", trace_id="t"
        )
        service.approve_guestbook_entry(entry.id)
        with pytest.raises(NotFoundError):
            service.approve_guestbook_entry(entry.id)


# ---------------------------------------------------------------------------
# Check-in stats
# ---------------------------------------------------------------------------


class TestCheckinStats:
    def test_most_frequent_visitor_counts_enrolled_only(self) -> None:
        service = make_service()
        for i in range(3):
            service.create_checkin(
                person_id="prs_alex",
                label="Alex",
                ip=f"10.0.0.{i}",
                session_token=f"s{i}",
                trace_id="t",
            )
        for i in range(2):
            service.create_checkin(
                person_id=None,
                label="anon guest",
                ip=f"10.0.1.{i}",
                session_token=f"a{i}",
                trace_id="t",
            )
        stat = service.most_frequent_visitor_stat()
        assert stat is not None
        assert stat["person_id"] == "prs_alex"
        assert stat["count"] == 3
        assert stat["label"] == "Alex"

    def test_no_stat_when_no_enrolled_checkins(self) -> None:
        service = make_service()
        service.create_checkin(
            person_id=None, label="anon", ip="10.0.0.1", session_token="s1", trace_id="t"
        )
        assert service.most_frequent_visitor_stat() is None


# ---------------------------------------------------------------------------
# Check-in photo reference (ADR-0013)
# ---------------------------------------------------------------------------


class TestCheckinPhotoReference:
    def test_checkin_stores_returns_and_emits_photo_recording_id(self) -> None:
        service = make_service()
        checkin = service.create_checkin(
            person_id="prs_alex",
            label="Alex",
            photo_recording_id="rec_photo_123",
            ip="10.0.0.1",
            session_token="s1",
            trace_id="t",
        )
        # Returned from create + persisted for reads.
        assert checkin.photo_recording_id == "rec_photo_123"
        listed = service.list_checkins(limit=10, cursor=None)
        assert listed[0].photo_recording_id == "rec_photo_123"

        # Emitted on the contract event.
        event = parse_event(service.events[-1])  # type: ignore[attr-defined]
        assert event.type == "social.checkin_created"
        assert event.payload.photo_recording_id == "rec_photo_123"  # type: ignore[union-attr]

    def test_checkin_without_photo_defaults_to_none(self) -> None:
        service = make_service()
        checkin = service.create_checkin(
            person_id=None,
            label="anon",
            ip="10.0.0.1",
            session_token="s1",
            trace_id="t",
        )
        assert checkin.photo_recording_id is None
        listed = service.list_checkins(limit=10, cursor=None)
        assert listed[0].photo_recording_id is None

        event = parse_event(service.events[-1])  # type: ignore[attr-defined]
        assert event.payload.photo_recording_id is None  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Visitor collage aggregate stats (year-end "who's stopped by" tile)
# ---------------------------------------------------------------------------


class TestVisitorCollageStats:
    def test_empty_store_returns_zeroed_stats(self) -> None:
        stats = make_service().visitor_collage_stats()
        assert stats["total_checkins"] == 0
        assert stats["checkins_this_year"] == 0
        assert stats["unique_visitors"] == 0
        assert stats["most_frequent"] is None
        assert stats["first_checkin_at"] is None
        assert stats["most_recent_checkin_at"] is None

    def test_unique_visitors_groups_enrolled_and_counts_guests(self) -> None:
        service = make_service()
        for i in range(3):
            service.create_checkin(
                person_id="prs_alex",
                label="Alex",
                ip=f"10.0.0.{i}",
                session_token=f"s{i}",
                trace_id="t",
            )
        for i in range(2):
            service.create_checkin(
                person_id=None,
                label="guest",
                ip=f"10.0.1.{i}",
                session_token=f"a{i}",
                trace_id="t",
            )
        stats = service.visitor_collage_stats()
        assert stats["total_checkins"] == 5
        # One enrolled person (distinct) + two anonymous guests = three visitors.
        assert stats["distinct_visitors"] == 1
        assert stats["guest_count"] == 2
        assert stats["unique_visitors"] == 3
        # most_frequent exposes only the freeform label + count, never person_id.
        assert stats["most_frequent"] == {"label": "Alex", "count": 3}
        assert "person_id" not in stats["most_frequent"]

    def test_checkins_this_year_excludes_prior_years(self) -> None:
        service = make_service()
        # Two old check-ins inserted straight through the store with a past date.
        for i in range(2):
            service.store.insert_checkin(
                checkin_id=f"old-{i}",
                person_id=None,
                label="old guest",
                photo_recording_id=None,
                session_key_hash="h",
                created_at="2020-06-01T12:00:00Z",
            )
        service.create_checkin(
            person_id=None, label="recent", ip="10.0.0.9", session_token="s9", trace_id="t"
        )
        stats = service.visitor_collage_stats()
        assert stats["total_checkins"] == 3
        assert stats["checkins_this_year"] == 1
        assert stats["first_checkin_at"] == "2020-06-01T12:00:00Z"

    def test_soft_deleted_checkins_are_excluded(self) -> None:
        service = make_service()
        checkin = service.create_checkin(
            person_id="prs_alex", label="Alex", ip="10.0.0.1", session_token="s1", trace_id="t"
        )
        service.request_deletion(
            target_kind="checkin",
            target_id=checkin.id,
            ip="10.0.0.1",
            session_token="admin",
            trace_id="t",
            actor="admin",
        )
        stats = service.visitor_collage_stats()
        assert stats["total_checkins"] == 0
        assert stats["most_frequent"] is None


class TestListCheckinPhotos:
    def test_only_returns_photo_checkins_newest_first(self) -> None:
        service = make_service()
        # Explicit timestamps through the store so ordering is deterministic
        # (create_checkin stamps millisecond-precision "now", which can collide).
        service.store.insert_checkin(
            checkin_id="c-nophoto",
            person_id=None,
            label="no photo",
            photo_recording_id=None,
            session_key_hash="h",
            created_at="2026-01-01T00:00:00Z",
        )
        service.store.insert_checkin(
            checkin_id="c-old",
            person_id=None,
            label="first",
            photo_recording_id="rec_1",
            session_key_hash="h",
            created_at="2026-02-01T00:00:00Z",
        )
        service.store.insert_checkin(
            checkin_id="c-new",
            person_id=None,
            label="second",
            photo_recording_id="rec_2",
            session_key_hash="h",
            created_at="2026-03-01T00:00:00Z",
        )
        photos = service.list_checkin_photos(limit=10)
        # Only the two photo check-ins, newest first.
        assert [c.photo_recording_id for c in photos] == ["rec_2", "rec_1"]

    def test_soft_deleted_photo_checkins_are_excluded(self) -> None:
        service = make_service()
        checkin = service.create_checkin(
            person_id=None,
            label="gone",
            photo_recording_id="rec_x",
            ip="10.0.0.1",
            session_token="s1",
            trace_id="t",
        )
        service.request_deletion(
            target_kind="checkin",
            target_id=checkin.id,
            ip="10.0.0.1",
            session_token="admin",
            trace_id="t",
            actor="admin",
        )
        assert service.list_checkin_photos(limit=10) == []


# --- visitor streak (#19) ---------------------------------------------------
#
# Counts over *dates*, never over people: the streak says "someone came by on N
# consecutive days", which is the celebratory fact, and needs no person_id to say
# it (ARCHITECTURE.md §9, ADR-0018 E-24).


class TestDayStreaks:
    @staticmethod
    def _stamps(*days: int, hour: int = 12) -> list[str]:
        return [f"2026-09-{d:02d}T{hour:02d}:00:00.000Z" for d in days]

    def test_counts_a_run_ending_today(self) -> None:
        current, longest = compute_day_streaks(self._stamps(8, 9, 10), today=date(2026, 9, 10))
        assert (current, longest) == (3, 3)

    def test_a_run_through_yesterday_is_still_alive(self) -> None:
        """A tile that resets at 00:01 every night reads as broken, not accurate."""
        current, _ = compute_day_streaks(self._stamps(8, 9), today=date(2026, 9, 10))
        assert current == 2

    def test_two_empty_days_ends_the_current_streak(self) -> None:
        current, longest = compute_day_streaks(self._stamps(1, 2, 3), today=date(2026, 9, 10))
        assert current == 0, "the streak is over"
        assert longest == 3, "but the record stands"

    def test_the_longest_run_survives_a_later_gap(self) -> None:
        current, longest = compute_day_streaks(
            self._stamps(1, 2, 3, 4, 9, 10), today=date(2026, 9, 10)
        )
        assert (current, longest) == (2, 4)

    def test_several_checkins_on_one_day_are_still_one_day(self) -> None:
        stamps = self._stamps(9, hour=9) + self._stamps(9, hour=18) + self._stamps(10)
        assert compute_day_streaks(stamps, today=date(2026, 9, 10)) == (2, 2)

    def test_no_checkins_is_not_a_streak_of_one(self) -> None:
        assert compute_day_streaks([], today=date(2026, 9, 10)) == (0, 0)

    def test_one_unparseable_row_does_not_cost_the_streak(self) -> None:
        """Same rule as ADR-0039's date table: skip the bad row, keep the rest."""
        stamps = ["not-a-timestamp", *self._stamps(9, 10)]
        assert compute_day_streaks(stamps, today=date(2026, 9, 10)) == (2, 2)

    def test_days_are_counted_in_local_time_not_utc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The off-by-one nobody notices until the morning the streak is wrong.

        Two check-ins on one local evening, either side of UTC midnight. Grouped
        locally that is ONE day and no streak; grouped by UTC it is two consecutive
        days and the tile claims a 2-day run that never happened. ADR-0039 hit this
        exact class of bug and made the rule local.

        The timezone is pinned rather than inherited: on a UTC runner an inherited
        one makes both groupings agree and this test quietly stops testing anything.
        """
        monkeypatch.setenv("TZ", "America/Los_Angeles")
        time.tzset()
        try:
            # 17:00Z = 10:00 PDT Sep 9; 01:00Z next day = 18:00 PDT, still Sep 9.
            stamps = ["2026-09-09T17:00:00.000Z", "2026-09-10T01:00:00.000Z"]
            current, longest = compute_day_streaks(stamps, today=date(2026, 9, 9))
            assert longest == 1, "one local evening is one day, not a 2-day streak"
            assert current == 1
        finally:
            monkeypatch.undo()
            time.tzset()


class TestCollageStreakWiring:
    def test_the_collage_reports_a_streak_and_no_person_id(self) -> None:
        service = make_service()
        service.create_checkin(
            person_id="prs_alex", label="Alex", ip="10.0.0.1", session_token="s1", trace_id="t"
        )
        stats = service.visitor_collage_stats()

        assert "current_streak_days" in stats
        assert "longest_streak_days" in stats
        assert stats["longest_streak_days"] == 1, "one check-in today is a one-day run"
        # The streak must not smuggle identity onto a surface built from counts.
        assert "person_id" not in json.dumps(stats)

    def test_an_empty_door_reports_zero_not_one(self) -> None:
        stats = make_service().visitor_collage_stats()
        assert stats["current_streak_days"] == 0
        assert stats["longest_streak_days"] == 0
