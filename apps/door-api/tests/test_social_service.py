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
from door_api.social.service import SocialService
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
