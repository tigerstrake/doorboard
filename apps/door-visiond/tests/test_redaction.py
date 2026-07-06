"""ADR-0009 E-3: biometric log-redaction filter behaviour."""

from __future__ import annotations

import logging

from doorboard_observability.redaction import (
    REDACTED,
    BiometricRedactionFilter,
    redact_value,
)

from .conftest import capture_logs


def _record(msg: str = "m", **extra: object) -> logging.LogRecord:
    rec = logging.LogRecord("door_visiond.test", logging.INFO, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(rec, key, value)
    return rec


def test_long_byte_string_redacted() -> None:
    assert redact_value(b"x" * 65) == REDACTED
    assert redact_value(b"x" * 64) == b"x" * 64  # boundary kept


def test_long_number_sequence_redacted() -> None:
    assert redact_value([0.1] * 17) == REDACTED
    assert redact_value([0.1] * 16) == [0.1] * 16  # boundary kept


def test_denylist_keys_redacted() -> None:
    out = redact_value({"vector": [1, 2], "count": 3})
    assert out == {"vector": REDACTED, "count": 3}


def test_long_base64ish_string_redacted() -> None:
    blob = "QUJD" * 30  # base64 chars, > 64 long
    assert redact_value(blob) == REDACTED
    assert redact_value("a short human message") == "a short human message"


def test_filter_scrubs_extra_fields_in_place() -> None:
    filt = BiometricRedactionFilter()
    rec = _record(embedding=[0.1] * 512, event_id="evt-1")
    assert filt.filter(rec) is True
    assert getattr(rec, "embedding", None) == REDACTED
    assert getattr(rec, "event_id", None) == "evt-1"


def test_installed_on_visiond_logger_tree() -> None:
    logger = logging.getLogger("door_visiond.redaction_probe")
    # get_logger attaches the filter; simulate a module using it.
    from door_visiond.logging_setup import get_logger

    logger = get_logger("door_visiond.redaction_probe")
    with capture_logs("door_visiond") as records:
        logger.info("attempted leak", extra={"vector": [3.14159] * 128})
    assert records
    leaked = [r for r in records if getattr(r, "vector", None) not in (None, REDACTED)]
    assert leaked == []
    for record in records:
        assert getattr(record, "vector", REDACTED) == REDACTED
