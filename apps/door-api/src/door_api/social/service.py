"""Business logic for guestbook/poll/checkin CRUD, safety rails, and moderation.

Wraps ``SocialStore`` with sanitization, rate limiting, event emission
(``social.*``), and the moderation audit log. Mirrors the on_event callback
pattern already established by ``door_api.session.SessionMachine`` so both
feed the same WebSocket broadcast.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from doorboard_contracts.events import (
    BaseEvent,
    SocialCheckinCreatedEvent,
    SocialCheckinCreatedPayload,
    SocialDeletionRequestedEvent,
    SocialDeletionRequestedPayload,
    SocialGuestbookEntryCreatedEvent,
    SocialGuestbookEntryCreatedPayload,
    SocialPollVoteCastEvent,
    SocialPollVoteCastPayload,
)
from doorboard_esp32_link.esp32 import uuid7_now

from door_api.social.config import SocialConfig
from door_api.social.errors import (
    AlreadyVotedError,
    NotFoundError,
    PollClosedError,
    RateLimitedError,
    UnsupportedDeletionTargetError,
)
from door_api.social.ratelimit import CompositeRateLimiter, RateLimiter
from door_api.social.sanitize import SanitizationError, sanitize_optional_text, sanitize_text
from door_api.social.store import Checkin, GuestbookEntry, Poll, SocialStore

logger = logging.getLogger("door-api.social")

# target_kind values this service knows how to delete. video_message/photo/
# enrollment are owned by other services (door-media, gallery, door-visiond)
# and are rejected here with a clear error rather than silently ignored.
_DELETABLE_KINDS = ("guestbook", "checkin")

type EventCallback = Callable[[dict[str, Any]], None]


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def hash_ip(ip: str) -> str:
    """One-way hash of the caller's IP for moderation/rate-limit records.

    Never store the raw IP — a hash is enough to correlate abuse without
    retaining a durable identifier.
    """
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:32]


def hash_session_key(session_key: str) -> str:
    return hashlib.sha256(session_key.encode("utf-8")).hexdigest()


@dataclass
class SocialMetrics:
    guestbook_created: int = 0
    guestbook_rejected_invalid: int = 0
    guestbook_rejected_rate_limited: int = 0
    poll_votes_cast: int = 0
    poll_votes_rejected: int = 0
    checkins_created: int = 0
    checkins_rejected: int = 0
    deletion_requests: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "social_guestbook_created_total": self.guestbook_created,
            "social_guestbook_rejected_invalid_total": self.guestbook_rejected_invalid,
            "social_guestbook_rejected_rate_limited_total": self.guestbook_rejected_rate_limited,
            "social_poll_votes_cast_total": self.poll_votes_cast,
            "social_poll_votes_rejected_total": self.poll_votes_rejected,
            "social_checkins_created_total": self.checkins_created,
            "social_checkins_rejected_total": self.checkins_rejected,
            "social_deletion_requests_total": self.deletion_requests,
        }


@dataclass
class SocialService:
    config: SocialConfig
    store: SocialStore
    on_event: EventCallback
    metrics: SocialMetrics = field(default_factory=SocialMetrics)

    _write_limiter: CompositeRateLimiter = field(init=False)

    def __post_init__(self) -> None:
        self._write_limiter = CompositeRateLimiter(
            per_ip=RateLimiter(
                limit=self.config.write_rate_limit_count,
                window_s=self.config.write_rate_limit_window_s,
            ),
            per_session=RateLimiter(
                limit=self.config.write_rate_limit_count,
                window_s=self.config.write_rate_limit_window_s,
            ),
        )

    def set_now_fn(self, fn: Callable[[], float]) -> None:
        """Override the rate limiter's clock source (for testing)."""
        self._write_limiter.per_ip.now_fn = fn
        self._write_limiter.per_session.now_fn = fn

    def _check_rate_limit(self, *, ip: str, session_token: str) -> None:
        if not self._write_limiter.allow(ip_key=hash_ip(ip), session_key=session_token):
            logger.warning(
                json.dumps(
                    {
                        "service": "door-api",
                        "event_id": "social_rate_limited",
                        "ip_hash": hash_ip(ip),
                    }
                )
            )
            raise RateLimitedError(
                f"rate limit exceeded: max {self.config.write_rate_limit_count} writes "
                f"per {self.config.write_rate_limit_window_s:.0f}s"
            )

    def _emit(self, event_type: str, payload_model: Any, *, trace_id: str) -> None:
        try:
            contract_trace_id = UUID(trace_id)
        except ValueError:
            contract_trace_id = uuid5(NAMESPACE_URL, trace_id)

        event_type_map: dict[str, type[BaseEvent]] = {
            "social.guestbook_entry_created": SocialGuestbookEntryCreatedEvent,
            "social.poll_vote_cast": SocialPollVoteCastEvent,
            "social.checkin_created": SocialCheckinCreatedEvent,
            "social.deletion_requested": SocialDeletionRequestedEvent,
        }
        event = event_type_map[event_type].model_validate(
            {
                "event_id": uuid7_now(),
                "type": event_type,
                "source": "door-api",
                "occurred_at": datetime.now(UTC),
                "monotonic_ms": int(time.monotonic() * 1000),
                "door_id": self.config.door_id,
                "trace_id": contract_trace_id,
                "payload": payload_model,
            }
        )
        self.on_event(event.model_dump(mode="json"))

    def _log_moderation(self, *, target_kind: str, target_id: str, action: str, actor: str) -> None:
        self.store.append_moderation_log(
            log_id=str(uuid4()),
            target_kind=target_kind,
            target_id=target_id,
            action=action,
            actor=actor,
            created_at=_utcnow_iso(),
        )

    # ------------------------------------------------------------------
    # Guestbook
    # ------------------------------------------------------------------

    def create_guestbook_entry(
        self,
        *,
        text: str,
        author_label: str | None,
        ip: str,
        session_token: str,
        trace_id: str,
    ) -> GuestbookEntry:
        try:
            self._check_rate_limit(ip=ip, session_token=session_token)
        except RateLimitedError:
            self.metrics.guestbook_rejected_rate_limited += 1
            raise
        try:
            clean_text = sanitize_text(
                text, max_len=self.config.guestbook_text_max_len, field_name="text"
            )
            clean_author = sanitize_optional_text(
                author_label,
                max_len=self.config.guestbook_author_label_max_len,
                field_name="author_label",
            )
        except SanitizationError:
            self.metrics.guestbook_rejected_invalid += 1
            raise

        entry_uuid = uuid4()
        entry_id = str(entry_uuid)
        created_at = _utcnow_iso()
        self.store.insert_guestbook_entry(
            entry_id=entry_id,
            text=clean_text,
            author_label=clean_author,
            status="pending",
            ip_hash=hash_ip(ip),
            session_key_hash=hash_session_key(session_token),
            created_at=created_at,
        )
        self.metrics.guestbook_created += 1
        self._log_moderation(
            target_kind="guestbook", target_id=entry_id, action="created", actor="visitor"
        )
        self._emit(
            "social.guestbook_entry_created",
            SocialGuestbookEntryCreatedPayload(
                entry_id=entry_uuid, text=clean_text, author_label=clean_author
            ),
            trace_id=trace_id,
        )
        entry = self.store.get_guestbook_entry(entry_id)
        assert entry is not None
        return entry

    def list_public_guestbook_entries(
        self, *, limit: int, cursor: str | None
    ) -> list[GuestbookEntry]:
        return self.store.list_guestbook_entries(
            status="approved", limit=limit, cursor_created_at=cursor
        )

    def list_admin_guestbook_entries(
        self, *, status: str, limit: int, cursor: str | None
    ) -> list[GuestbookEntry]:
        if status not in ("pending", "approved"):
            raise ValueError("status must be 'pending' or 'approved'")
        return self.store.list_guestbook_entries(
            status=status, limit=limit, cursor_created_at=cursor
        )

    def approve_guestbook_entry(self, entry_id: str, *, actor: str = "admin") -> None:
        ok = self.store.approve_guestbook_entry(entry_id)
        if not ok:
            raise NotFoundError(f"no pending guestbook entry {entry_id}")
        self._log_moderation(
            target_kind="guestbook", target_id=entry_id, action="approved", actor=actor
        )

    def delete_guestbook_entry(self, entry_id: str, *, actor: str = "admin") -> None:
        ok = self.store.soft_delete_guestbook_entry(entry_id, deleted_at=_utcnow_iso())
        if not ok:
            raise NotFoundError(f"no guestbook entry {entry_id}")
        self._log_moderation(
            target_kind="guestbook", target_id=entry_id, action="deleted", actor=actor
        )

    # ------------------------------------------------------------------
    # Polls
    # ------------------------------------------------------------------

    def create_poll(self, *, question: str, options: list[str], actor: str = "admin") -> Poll:
        clean_question = sanitize_text(
            question, max_len=self.config.poll_question_max_len, field_name="question"
        )
        if len(options) < 2:
            raise SanitizationError("a poll needs at least 2 options")
        clean_options = [
            sanitize_text(o, max_len=self.config.poll_option_max_len, field_name="option")
            for o in options
        ]
        poll_id = str(uuid4())
        self.store.insert_poll(
            poll_id=poll_id,
            question=clean_question,
            options=[(str(uuid4()), text) for text in clean_options],
            created_at=_utcnow_iso(),
        )
        self._log_moderation(target_kind="poll", target_id=poll_id, action="created", actor=actor)
        poll = self.store.get_poll(poll_id)
        assert poll is not None
        return poll

    def get_current_poll(self) -> Poll | None:
        return self.store.get_current_poll()

    def list_polls(self, *, limit: int) -> list[Poll]:
        return self.store.list_polls(limit=limit)

    def close_poll(self, poll_id: str, *, actor: str = "admin") -> None:
        ok = self.store.close_poll(poll_id, closed_at=_utcnow_iso())
        if not ok:
            raise NotFoundError(f"no open poll {poll_id}")
        self._log_moderation(target_kind="poll", target_id=poll_id, action="closed", actor=actor)

    def cast_vote(
        self,
        *,
        poll_id: str,
        option_id: str,
        ip: str,
        session_token: str,
        trace_id: str,
    ) -> None:
        try:
            self._check_rate_limit(ip=ip, session_token=session_token)
        except RateLimitedError:
            self.metrics.poll_votes_rejected += 1
            raise

        poll = self.store.get_poll(poll_id)
        if poll is None:
            self.metrics.poll_votes_rejected += 1
            raise NotFoundError(f"no poll {poll_id}")
        if poll.status != "open":
            self.metrics.poll_votes_rejected += 1
            raise PollClosedError(f"poll {poll_id} is closed")
        if not self.store.option_belongs_to_poll(poll_id=poll_id, option_id=option_id):
            self.metrics.poll_votes_rejected += 1
            raise NotFoundError(f"option {option_id} does not belong to poll {poll_id}")

        inserted = self.store.insert_vote(
            poll_id=poll_id,
            session_token=hash_session_key(session_token),
            option_id=option_id,
            created_at=_utcnow_iso(),
        )
        if not inserted:
            self.metrics.poll_votes_rejected += 1
            raise AlreadyVotedError(f"session already voted in poll {poll_id}")

        self.metrics.poll_votes_cast += 1
        self._emit(
            "social.poll_vote_cast",
            SocialPollVoteCastPayload(poll_id=poll_id, option_id=option_id),
            trace_id=trace_id,
        )

    def poll_results(self, poll_id: str) -> list[dict[str, Any]]:
        poll = self.store.get_poll(poll_id)
        if poll is None:
            raise NotFoundError(f"no poll {poll_id}")
        tally = self.store.poll_results(poll_id)
        return [
            {"option_id": opt.id, "text": opt.text, "votes": tally.get(opt.id, 0)}
            for opt in poll.options
        ]

    # ------------------------------------------------------------------
    # Check-ins
    # ------------------------------------------------------------------

    def create_checkin(
        self,
        *,
        person_id: str | None,
        label: str | None,
        ip: str,
        session_token: str,
        trace_id: str,
    ) -> Checkin:
        try:
            self._check_rate_limit(ip=ip, session_token=session_token)
        except RateLimitedError:
            self.metrics.checkins_rejected += 1
            raise
        try:
            clean_label = sanitize_optional_text(
                label, max_len=self.config.checkin_label_max_len, field_name="label"
            )
        except SanitizationError:
            self.metrics.checkins_rejected += 1
            raise

        checkin_uuid = uuid4()
        checkin_id = str(checkin_uuid)
        self.store.insert_checkin(
            checkin_id=checkin_id,
            person_id=person_id,
            label=clean_label,
            session_key_hash=hash_session_key(session_token),
            created_at=_utcnow_iso(),
        )
        self.metrics.checkins_created += 1
        self._emit(
            "social.checkin_created",
            SocialCheckinCreatedPayload(
                checkin_id=checkin_uuid, person_id=person_id, label=clean_label
            ),
            trace_id=trace_id,
        )
        checkin = self.store.get_checkin(checkin_id)
        assert checkin is not None
        return checkin

    def list_checkins(self, *, limit: int, cursor: str | None) -> list[Checkin]:
        return self.store.list_checkins(limit=limit, cursor_created_at=cursor)

    def most_frequent_visitor_stat(self) -> dict[str, Any] | None:
        """Playful most-frequent-visitor stat, voluntary check-ins only."""
        top = self.store.most_frequent_checkin_person()
        if top is None:
            return None
        person_id, count = top
        label = self.store.latest_label_for_person(person_id)
        return {"person_id": person_id, "label": label, "count": count}

    # ------------------------------------------------------------------
    # Deletion (visitor-initiated request OR admin-direct action)
    # ------------------------------------------------------------------

    def request_deletion(
        self,
        *,
        target_kind: str,
        target_id: str,
        ip: str,
        session_token: str,
        trace_id: str,
        actor: str = "visitor",
    ) -> None:
        if target_kind not in _DELETABLE_KINDS:
            raise UnsupportedDeletionTargetError(
                f"target_kind '{target_kind}' is not handled by door-api's social module"
            )

        # Only rate-limit visitor-initiated requests — admin moderation actions
        # (approve/delete from the /admin panel) are authenticated separately
        # and must not be throttled by the public write limit.
        if actor == "visitor":
            self._check_rate_limit(ip=ip, session_token=session_token)

        if target_kind == "guestbook":
            if actor == "visitor":
                ok = self.store.soft_delete_guestbook_entry(
                    target_id,
                    deleted_at=_utcnow_iso(),
                    session_key_hash=hash_session_key(session_token),
                )
                if not ok:
                    raise NotFoundError(f"no guestbook entry {target_id} owned by this session")
                self._log_moderation(
                    target_kind="guestbook",
                    target_id=target_id,
                    action="deleted",
                    actor=actor,
                )
            else:
                self.delete_guestbook_entry(target_id, actor=actor)
        elif target_kind == "checkin":
            ok = self.store.soft_delete_checkin(
                target_id,
                deleted_at=_utcnow_iso(),
                session_key_hash=(hash_session_key(session_token) if actor == "visitor" else None),
            )
            if not ok:
                raise NotFoundError(f"no checkin {target_id}")
            self._log_moderation(
                target_kind="checkin", target_id=target_id, action="deleted", actor=actor
            )

        self.metrics.deletion_requests += 1
        self._emit(
            "social.deletion_requested",
            SocialDeletionRequestedPayload(target_kind=target_kind, target_id=target_id),
            trace_id=trace_id,
        )

    def moderation_log(self, *, limit: int) -> list[dict[str, Any]]:
        return self.store.list_moderation_log(limit=limit)
