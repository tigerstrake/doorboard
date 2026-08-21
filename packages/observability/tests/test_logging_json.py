"""Structured JSON logging (the diagnosability fix).

The regression these guard: an enrollment failed on real hardware and the log line
was just ``relay_enroll_failed``. The cause was attached via ``extra`` and thrown
away by a format string that never named it, so the bug had to be reproduced
locally rather than read out of the journal.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from doorboard_observability.logging_json import JsonLogFormatter, json_logging_config
from doorboard_observability.redaction import redaction_filter


def _render(record: logging.LogRecord, service: str = "door-visiond") -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(JsonLogFormatter(service).format(record))
    return payload


def _record(msg: str = "event", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("door_visiond.test", logging.INFO, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_extra_fields_survive_into_the_log_line() -> None:
    """The whole point: the field that used to vanish."""
    payload = _render(_record("relay_enroll_failed", error_class="IntegrityError"))
    assert payload["message"] == "relay_enroll_failed"
    assert payload["error_class"] == "IntegrityError"


def test_base_fields_are_present() -> None:
    payload = _render(_record("visit_opened"))
    assert payload["service"] == "door-visiond"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "door_visiond.test"
    assert "time" in payload


def test_the_conventional_diagnostic_fields_can_appear() -> None:
    """CONTRIBUTING asks for these in every line; the old formatter made it impossible."""
    payload = _render(
        _record(
            "session_transition",
            trace_id="0199-abc",
            session_id="ses_1",
            event_id="evt_1",
            latency_ms=12.5,
        )
    )
    assert payload["trace_id"] == "0199-abc"
    assert payload["session_id"] == "ses_1"
    assert payload["event_id"] == "evt_1"
    assert payload["latency_ms"] == 12.5


def test_a_message_with_quotes_stays_valid_json() -> None:
    """The old formatter interpolated unescaped into a JSON string literal."""
    nasty = 'he said "hi"\\ then a newline\nand a tab\t'
    payload = _render(_record(nasty))
    assert payload["message"] == nasty


def test_an_extra_cannot_shadow_a_base_field() -> None:
    """extra={"level": "DEBUG"} must not rewrite the record's real level."""
    payload = _render(_record("x", level="TOTALLY_FINE", service="not-this"))
    assert payload["level"] == "INFO"
    assert payload["service"] == "door-visiond"
    assert payload["extra_level"] == "TOTALLY_FINE"
    assert payload["extra_service"] == "not-this"


def test_unserialisable_values_degrade_instead_of_raising() -> None:
    """A log line must never be the reason a request fails."""

    class Opaque:
        def __repr__(self) -> str:
            return "<opaque>"

    payload = _render(_record("x", thing=Opaque()))
    assert payload["thing"] == "<opaque>"


def test_exceptions_are_included() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "door_visiond.test", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )
    payload = _render(record)
    assert "ValueError: boom" in payload["exception"]


def test_redaction_still_applies_to_the_fields_now_emitted() -> None:
    """ADR-0009 E-3: emitting extras must not become a biometric leak.

    The filter already scrubbed extras; it was the formatter discarding them. Now
    that they are emitted, prove the scrubbing is what reaches the line.
    """
    sentinel = b"DOORBOARD_SENTINEL_" + b"x" * 64
    record = _record("embedded", embedding=sentinel, vector=[0.1] * 512, person_id="prs_ok")
    assert redaction_filter().filter(record) is True

    payload = _render(record)
    rendered = json.dumps(payload)
    assert "DOORBOARD_SENTINEL_" not in rendered
    assert "0.1" not in rendered
    # Non-biometric context is still useful and still there.
    assert payload["person_id"] == "prs_ok"


def test_config_builder_wires_the_formatter_and_filters() -> None:
    config = json_logging_config("door-api")
    assert config["formatters"]["json"]["()"] is JsonLogFormatter
    assert config["formatters"]["json"]["service"] == "door-api"
    assert config["handlers"]["stdout"]["filters"] == []

    with_filter = json_logging_config(
        "door-visiond", filters={"biometric_redaction": {"()": redaction_filter}}
    )
    assert with_filter["handlers"]["stdout"]["filters"] == ["biometric_redaction"]


def test_config_actually_applies_end_to_end(capsys: pytest.CaptureFixture[str]) -> None:
    """dictConfig accepts it and a real logger call produces parseable JSON."""
    import logging.config

    logging.config.dictConfig(json_logging_config("door-visiond"))
    logging.getLogger("door_visiond.e2e").info("relay_enroll_failed", extra={"error_class": "X"})
    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["error_class"] == "X"
    assert payload["service"] == "door-visiond"


def test_httpx_request_logging_is_silenced(capsys: pytest.CaptureFixture[str]) -> None:
    """httpx's INFO line carries the full URL — and Telegram puts the bot token in it.

    Asserted here rather than trusted to a comment: this is the difference between
    a notification and a credential written to disk on every ring.
    """
    logging.config.dictConfig(json_logging_config("control-plane-api"))
    logging.getLogger("httpx").info(
        'HTTP Request: POST https://api.telegram.org/bot123:SECRET/sendMessage "200 OK"'
    )
    captured = capsys.readouterr().out
    assert captured == "", "httpx request lines must not reach the log stream"

    # A real failure still gets through.
    logging.getLogger("httpx").warning("connect_timeout")
    assert "connect_timeout" in capsys.readouterr().out
