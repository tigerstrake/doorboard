"""Biometric fence — structural guarantee that door-sync never syncs identity data.

ADR-0009 places the enrollment DB, embedding vectors, and transient face crops
under ``${SSD_DATA_ROOT}/visiond/`` (``enrollment.sqlite`` + ``visiond/tmp/``).
door-sync must be *structurally* incapable of shipping any of that off the Pi
(README "Must never … sync enrollment embeddings"; T-502 brief "biometric
fence").

The mechanism is a positive allowlist, not a denylist: a path may be enqueued
for upload only if, after resolution, it lives inside one of a small set of
explicitly syncable roots (``recordings``, ``thumbnails`` by default). Anything
else — the ``visiond`` tree, a symlink pointing out of the allowed roots, a
``..`` traversal, an absolute path elsewhere on disk — raises
:class:`FenceViolation` and is never written to the queue. Because every media
enqueue funnels through :func:`resolve_syncable`, adding a syncable location is a
deliberate, reviewable edit to ``SYNC_SYNCABLE_ROOTS`` rather than something an
upload worker can reach by accident.
"""

from __future__ import annotations

from pathlib import Path

# Directory names that hold identity/biometric data and must never be syncable,
# regardless of allowlist configuration. Purely defensive: the allowlist already
# excludes them by omission, but a misconfiguration that added one here would be
# rejected at startup (see validate_roots) so the fence cannot be opened by a
# typo in an env var.
FORBIDDEN_ROOTS: frozenset[str] = frozenset({"visiond", "enrollment", "embeddings", "tmp"})


class FenceViolation(Exception):
    """Raised when a path outside the syncable allowlist is offered for upload."""


def validate_roots(roots: tuple[str, ...]) -> tuple[str, ...]:
    """Normalise + validate the configured syncable roots.

    Rejects empty configs, path separators (roots are single top-level
    directory names), and any name on :data:`FORBIDDEN_ROOTS`.
    """
    if not roots:
        msg = "SYNC_SYNCABLE_ROOTS must list at least one directory"
        raise ValueError(msg)
    cleaned: list[str] = []
    for raw in roots:
        name = raw.strip().strip("/")
        if not name or "/" in name or name in {".", ".."}:
            msg = f"invalid syncable root {raw!r}: must be a single directory name"
            raise ValueError(msg)
        if name in FORBIDDEN_ROOTS:
            msg = f"syncable root {name!r} holds biometric/identity data and is forbidden"
            raise ValueError(msg)
        cleaned.append(name)
    return tuple(cleaned)


def resolve_syncable(
    rel_path: str, *, ssd_data_root: Path, syncable_roots: tuple[str, ...]
) -> Path:
    """Resolve an SSD-relative media path and prove it is inside the allowlist.

    Returns the absolute, resolved path on success. Raises
    :class:`FenceViolation` if the path escapes ``ssd_data_root`` or does not
    sit under one of ``syncable_roots``.

    ``rel_path`` is expected to be SSD-relative (as door-media stores it). An
    absolute path is only accepted if it already lives under ``ssd_data_root``
    and an allowed root; otherwise it is rejected.
    """
    root = ssd_data_root.resolve(strict=False)
    candidate = Path(rel_path)
    combined = candidate if candidate.is_absolute() else root / candidate
    # resolve() collapses ``..`` and symlinks so traversal cannot smuggle a path
    # back out of an allowed root and into ``visiond``.
    resolved = combined.resolve(strict=False)

    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        msg = f"path {rel_path!r} escapes SSD data root"
        raise FenceViolation(msg) from exc

    parts = relative.parts
    if not parts:
        msg = f"path {rel_path!r} resolves to the SSD data root itself"
        raise FenceViolation(msg)

    top = parts[0]
    if top not in syncable_roots:
        msg = (
            f"path {rel_path!r} is under {top!r}, which is not a syncable root "
            f"({', '.join(syncable_roots)})"
        )
        raise FenceViolation(msg)

    return resolved
