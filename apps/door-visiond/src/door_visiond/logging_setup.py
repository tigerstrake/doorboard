"""Logger factory for door-visiond with biometric redaction always on.

Every door-visiond module MUST obtain its logger through :func:`get_logger`,
which attaches the ADR-0009 E-3 biometric redaction filter to *that* logger.
Because the filter runs inside ``Logger.handle`` (before any handler and before
propagation), a record emitted on a ``get_logger`` logger is scrubbed in place
for every downstream handler in every mode — including ``disabled``.

Coverage note: Python does NOT apply an ancestor logger's *filters* to records
emitted on its children — only handlers propagate up the tree, logger-level
filters do not. So the per-module ``get_logger`` install is what actually
guarantees coverage across the ``door_visiond`` tree; installing on the root
alone would leave child loggers unfiltered. A module that bypasses
``get_logger`` and calls ``logging.getLogger("door_visiond.x")`` directly would
NOT be redacted — always use ``get_logger``. Removing the filter is a
review-blocking defect (ADR-0009 §2).
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


# Install on the tree root at import time so records logged directly on
# getLogger("door_visiond") are covered. NOTE: filters are NOT inherited down the
# tree, so this does not cover child loggers ("door_visiond.x") — get_logger
# installs the filter on each module logger, which is what covers the tree.
install_biometric_redaction(_ROOT)
