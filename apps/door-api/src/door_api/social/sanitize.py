"""Sanitization for user-generated content.

Policy (GEMINI.md, handoff §13): store raw, escape at render; no HTML
rendering of UGC anywhere. This module only prepares text for storage —
it strips control characters and length-caps. It deliberately does NOT
HTML-escape: escaping happens once, at the render boundary, so a value
never gets double-escaped as it passes between services. The stored
value is therefore still "raw" and any consumer that renders it into
HTML (a browser DOM via React text nodes, an admin page, etc.) MUST
escape it there — never via ``dangerouslySetInnerHTML`` or string
concatenation into markup.

``escape_for_render`` is provided for the rare non-React rendering path
(e.g. a future server-rendered admin export) and is exercised by tests
to prove the injection corpus is inert.
"""

from __future__ import annotations

import html
import unicodedata

# Strip C0/C1 control characters except tab/newline (which we also strip,
# since none of our fields are multi-line) — keeps guestbook/checkin/poll
# text to a single visual line and prevents terminal/log injection via
# control sequences.
_ALLOWED_CATEGORIES_EXCLUDED = {"Cc", "Cf"}


class SanitizationError(ValueError):
    """Raised when input fails validation (empty after stripping, too long)."""


def sanitize_text(raw: str, *, max_len: int, field_name: str = "text") -> str:
    """Strip control characters and enforce a length cap.

    Raises SanitizationError on empty (post-strip) or over-length input.
    The caller is expected to turn this into a 422/400 response and count
    the rejection in metrics — never crash a loop or task on malformed input.
    """
    if not isinstance(raw, str):
        raise SanitizationError(f"{field_name} must be a string")

    cleaned = "".join(
        ch for ch in raw if unicodedata.category(ch) not in _ALLOWED_CATEGORIES_EXCLUDED
    )
    cleaned = cleaned.strip()

    if not cleaned:
        raise SanitizationError(f"{field_name} must not be empty")
    if len(cleaned) > max_len:
        raise SanitizationError(f"{field_name} exceeds {max_len} characters")

    return cleaned


def sanitize_optional_text(
    raw: str | None, *, max_len: int, field_name: str = "text"
) -> str | None:
    """Like sanitize_text but None/blank input passes through as None."""
    if raw is None:
        return None
    if raw.strip() == "":
        return None
    return sanitize_text(raw, max_len=max_len, field_name=field_name)


def escape_for_render(text: str) -> str:
    """HTML-escape at the render boundary. Never store the result."""
    return html.escape(text, quote=True)
