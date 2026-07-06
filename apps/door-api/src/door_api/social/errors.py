"""Domain errors for the social service, mapped to HTTP status in routes.py."""

from __future__ import annotations


class SocialError(Exception):
    """Base class for social-service domain errors."""


class RateLimitedError(SocialError):
    """Caller exceeded the configured write rate limit."""


class NotFoundError(SocialError):
    """Referenced poll/option/entry/checkin does not exist."""


class AlreadyVotedError(SocialError):
    """This session token already voted in this poll."""


class PollClosedError(SocialError):
    """Vote cast against a closed poll."""


class UnsupportedDeletionTargetError(SocialError):
    """Deletion request for a target_kind not owned by door-api's social module."""
