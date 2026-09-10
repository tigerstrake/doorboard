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

# Strip C0/C1 control characters (Cc, tab/newline included since none of our
# fields are multi-line) and format characters (Cf: bidi overrides, zero-width
# spaces, isolates) — keeps guestbook/checkin/poll text to a single visual line
# and prevents terminal/log injection via control sequences.
_STRIPPED_CATEGORIES = {"Cc", "Cf"}
# Two characters emoji sequences are made of, allow-listed through the strip:
# U+200D ZERO WIDTH JOINER is Cf, and dropping it turned 👨‍👩‍👧 into three separate
# people and 👩‍💻 into a woman beside a laptop; U+FE0F VARIATION SELECTOR-16 (Mn)
# is what makes ❤️ and 1️⃣ render as emoji. Neither is a bidi control or a line
# separator, and neither has a visual footprint of its own.
_EMOJI_GLUE = {"‍", "️"}
# Consecutive non-spacing combining marks (Mn) are capped so a "zalgo" line
# cannot stack hundreds of diacritics on one base character and overflow the
# wallboard tile vertically. Three covers every real orthography we would meet,
# and NFC has already composed what it can before this is applied.
_MAX_CONSECUTIVE_COMBINING = 3


class SanitizationError(ValueError):
    """Raised when input fails validation (empty after stripping, too long)."""


def sanitize_text(raw: str, *, max_len: int, field_name: str = "text") -> str:
    """Strip control characters, normalise, clamp combining marks, cap the length.

    Raises SanitizationError on empty (post-strip) or over-length input.
    The caller is expected to turn this into a 422/400 response and count
    the rejection in metrics — never crash a loop or task on malformed input.
    """
    if not isinstance(raw, str):
        raise SanitizationError(f"{field_name} must be a string")

    stripped = "".join(
        ch
        for ch in raw
        if ch in _EMOJI_GLUE or unicodedata.category(ch) not in _STRIPPED_CATEGORIES
    )
    # NFC *after* the strip: a format character removed from between a base and its
    # mark can expose a pair NFC composes, and the combining cap below should see the
    # composed form — what actually renders — rather than the decomposed input.
    composed = unicodedata.normalize("NFC", stripped)

    kept: list[str] = []
    combining_run = 0
    for ch in composed:
        if ch in _EMOJI_GLUE:
            # Neither counted nor a run reset: interleaving glue must not let a
            # zalgo line past the cap.
            kept.append(ch)
            continue
        if unicodedata.category(ch) == "Mn":
            combining_run += 1
            if combining_run > _MAX_CONSECUTIVE_COMBINING:
                continue
        else:
            combining_run = 0
        kept.append(ch)
    cleaned = "".join(kept).strip()

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
