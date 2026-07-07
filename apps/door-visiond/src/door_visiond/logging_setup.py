"""Logger factory for door-visiond with biometric redaction always on.

Every door-visiond module obtains its logger through :func:`get_logger`, which
attaches the ADR-0009 E-3 biometric redaction filter to that logger.  Because
the filter runs inside ``Logger.handle`` (before any handler and before
propagation), a record is scrubbed in place for every downstream handler in
every mode — including ``disabled``.  Removing this is a review-blocking
defect (ADR-0009 §2).
"""

from __future__ import annotations

import logging

from doorboard_observability.redaction import (
    BiometricRedactionFilter,
    install_biometric_redaction,
)

_ROOT = "door_visiond"


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``door_visiond`` tree with redaction installed."""
    logger = logging.getLogger(name)
    if not any(isinstance(f, BiometricRedactionFilter) for f in logger.filters):
        install_biometric_redaction(logger)
    return logger


# Install on the tree root at import time so even ad-hoc getLogger("door_visiond")
# callers inherit the filter.
install_biometric_redaction(_ROOT)
