"""Structured JSON logging, shared by every service.

Replaces five near-identical hardcoded format strings that each looked like:

    '{"time":"%(asctime)s","level":"%(levelname)s","service":"door-api",'
    '"logger":"%(name)s","message":"%(message)s"}'

That had two defects.

**It silently dropped every ``extra`` field.** ``logger.info("x", extra={...})``
puts those values on the LogRecord, but a format string that never names them
throws them away. So `error_class`, `person_id`, `trace_id`, latency — everything
the code carefully attached — vanished. This was found the slow way: an enrollment
failed on real hardware, the log said only ``relay_enroll_failed``, and the cause
had to be reproduced locally instead of read. CONTRIBUTING.md asks for
``service``, ``event_id``, ``trace_id``, ``session_id``, timestamps, latency and
error fields in every log line, and the old formatter made all of them impossible.

**It could emit invalid JSON.** The message was interpolated into a JSON string
with no escaping, so any message containing a quote, backslash or newline produced
a line no parser would accept. Event-name-style messages hid this; an exception
string would not have.

Redaction is unaffected and still applies: ``BiometricRedactionFilter`` already
scrubs ``extra`` attributes (ADR-0009 E-3), so the values this formatter now emits
have been through it. The filter was doing its job on fields the formatter was
discarding.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final

# Attributes a vanilla LogRecord always carries. Anything else came from
# ``extra=`` and is what we actually want to surface.
_STANDARD_ATTRS: Final[frozenset[str]] = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
) | {"message", "asctime", "taskName"}


def _jsonable(value: Any) -> Any:
    """Coerce a value into something ``json.dumps`` accepts.

    Falls back to ``repr`` rather than raising: a log line must never be the
    reason a request fails, and a slightly ugly value beats a lost diagnostic.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return repr(value)


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line, including caller-supplied ``extra`` fields."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Everything attached via extra=. Sorted so lines diff cleanly and are
        # predictable to eyeball across a long journal.
        for key in sorted(vars(record)):
            if key in _STANDARD_ATTRS or key.startswith("_"):
                continue
            # Never let an extra shadow a base field; a caller passing
            # extra={"level": ...} must not be able to rewrite the record's level.
            if key in payload:
                payload[f"extra_{key}"] = _jsonable(getattr(record, key))
            else:
                payload[key] = _jsonable(getattr(record, key))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # json.dumps handles the escaping the old format string did not.
        return json.dumps(payload, default=repr)


def json_logging_config(
    service: str,
    *,
    level: str = "INFO",
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A ``logging.config.dictConfig`` dict every service can share.

    ``filters`` lets a service add handler-level filters — door-visiond installs
    the biometric redaction filter here as defence in depth for any non-visiond
    logger that reaches stdout.
    """
    filter_names = sorted(filters or {})
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": dict(filters or {}),
        "formatters": {
            "json": {"()": JsonLogFormatter, "service": service},
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "json",
                "filters": filter_names,
            }
        },
        "root": {"level": level, "handlers": ["stdout"]},
    }
