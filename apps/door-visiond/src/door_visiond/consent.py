"""Consent-version resolution (ADR-0009 §5, mechanism E-7).

``docs/policies/consent-statement.md`` is the single consent source; its
``Version: vN`` tag is the value recorded at enrollment.  T-304's CLI/admin UI
render the file verbatim; T-302 only needs the current version to validate an
enrollment request's ``consent_version`` (stale → 409).
"""

from __future__ import annotations

import re
from pathlib import Path

_VERSION_RE = re.compile(r"Version:\s*(v\d+)", re.IGNORECASE)


def parse_consent_version(text: str) -> str | None:
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


def current_consent_version(*, statement_path: Path | None, fallback: str) -> str:
    """Return the current consent version.

    Reads the statement file when a path is configured and readable; otherwise
    falls back to the configured version (which defaults to the shipped v1).
    """
    if statement_path is not None:
        try:
            parsed = parse_consent_version(statement_path.read_text(encoding="utf-8"))
            if parsed is not None:
                return parsed
        except OSError:
            pass
    return fallback
