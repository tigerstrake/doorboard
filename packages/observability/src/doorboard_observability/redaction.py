"""Biometric log-redaction filter (ADR-0009 §2, mechanism E-3).

A ``logging.Filter`` that redacts anything that could be a face embedding,
raw frame, or other biometric payload *before* a log record reaches any
handler.  It is defensive in depth: door-visiond never intentionally logs a
vector, but a filter that scrubs the record guarantees a mistake upstream can
never leak biometric data to disk or a log aggregator.

Redaction rules (a value is scrubbed to ``"[REDACTED]"`` when):

- it is a byte string longer than 64 bytes;
- it is a sequence of more than 16 numbers (a candidate embedding vector);
- it is a base64-looking string longer than 64 characters;
- its mapping key is one of ``{"embedding", "vector", "face_crop", "frame"}``.

The filter mutates the record in place and always returns ``True`` — it never
drops a record, so operational context (event ids, counts, timings) survives
while biometric payloads do not.  Install it with
:func:`install_biometric_redaction` on the ``door_visiond`` logger tree; it is
on by default in every mode, including ``disabled`` (removing it is a
review-blocking defect per ADR-0009 §2).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Final, cast

REDACTED: Final[str] = "[REDACTED]"

DENYLIST_KEYS: Final[frozenset[str]] = frozenset({"embedding", "vector", "face_crop", "frame"})

MAX_BYTES: Final[int] = 64
MAX_FLOAT_SEQUENCE: Final[int] = 16
MAX_BASE64_CHARS: Final[int] = 64
_MAX_DEPTH: Final[int] = 8

# Base64 alphabet (standard + url-safe), padding and inner whitespace tolerated.
_BASE64_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9+/_=\-\s]+$")

# Attribute names present on a vanilla LogRecord — everything else is a
# caller-supplied "extra" that must be scanned.
_STANDARD_LOGRECORD_ATTRS: Final[frozenset[str]] = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
) | {"message", "asctime", "taskName"}


def _is_number_sequence(value: Sequence[object]) -> bool:
    """Return True if *value* is a non-empty sequence of only numbers (not bool)."""
    if not value:
        return False
    return all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)


def _is_long_base64ish(value: str) -> bool:
    return len(value) > MAX_BASE64_CHARS and bool(_BASE64_RE.match(value))


def redact_value(value: object, *, depth: int = 0) -> object:
    """Return *value* with any biometric-looking payload replaced by REDACTED."""
    if depth > _MAX_DEPTH:
        return REDACTED

    if isinstance(value, (bytes, bytearray)):
        return REDACTED if len(value) > MAX_BYTES else value
    if isinstance(value, memoryview):
        mv = cast("memoryview[int]", value)
        return REDACTED if mv.nbytes > MAX_BYTES else bytes(mv)

    if isinstance(value, str):
        return REDACTED if _is_long_base64ish(value) else value

    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        result: dict[object, object] = {}
        for key, item in mapping.items():
            if isinstance(key, str) and key.lower() in DENYLIST_KEYS:
                result[key] = REDACTED
            else:
                result[key] = redact_value(item, depth=depth + 1)
        return result

    # Ordered sequences (but not str/bytes, handled above).
    if isinstance(value, (list, tuple)):
        seq = cast("Sequence[object]", value)
        if _is_number_sequence(seq) and len(seq) > MAX_FLOAT_SEQUENCE:
            return REDACTED
        redacted = [redact_value(item, depth=depth + 1) for item in seq]
        return tuple(redacted) if isinstance(value, tuple) else redacted

    return value


class BiometricRedactionFilter(logging.Filter):
    """Scrub biometric-looking payloads from every log record (ADR-0009 E-3)."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API
        # 1. Positional/dict args used for %-formatting.
        if record.args:
            record.args = redact_value(record.args)  # type: ignore[assignment]

        # 2. Caller-supplied ``extra=...`` fields become record attributes.
        for key in list(vars(record)):
            if key in _STANDARD_LOGRECORD_ATTRS:
                continue
            if key.lower() in DENYLIST_KEYS:
                setattr(record, key, REDACTED)
            else:
                setattr(record, key, redact_value(getattr(record, key)))

        return True


# A single shared instance is enough; the filter is stateless.
_SHARED_FILTER: Final[BiometricRedactionFilter] = BiometricRedactionFilter()


def redaction_filter() -> BiometricRedactionFilter:
    """Return the shared biometric redaction filter instance."""
    return _SHARED_FILTER


def install_biometric_redaction(
    logger: logging.Logger | str = "door_visiond",
) -> BiometricRedactionFilter:
    """Attach the biometric redaction filter to *logger* (idempotent).

    Returns the installed filter.  Attaching at the logger level runs the
    filter inside ``Logger.handle`` — before any handler and before
    propagation — so the record is scrubbed in place for every downstream
    handler.  door-visiond attaches it to each of its module loggers so the
    whole tree is covered in every mode.
    """
    target = logging.getLogger(logger) if isinstance(logger, str) else logger
    if not any(isinstance(f, BiometricRedactionFilter) for f in target.filters):
        target.addFilter(_SHARED_FILTER)
    return _SHARED_FILTER
