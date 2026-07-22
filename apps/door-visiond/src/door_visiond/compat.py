"""Startup Hailo runtime + model compatibility check (T-302).

A hardware mode must report exactly the pinned runtime and model versions.  On
mismatch or absence the caller degrades to ``disabled`` (generic greetings) and
surfaces it via ``/health`` — it never blocks the door.  ``disabled``/``mock``
modes need no accelerator and always pass.

``detected`` is injectable so the startup check can be demonstrated against a
wrong-version fixture without hardware (T-302 acceptance).
"""

from __future__ import annotations

from dataclasses import dataclass

_HARDWARE_MODES = frozenset({"single-camera", "dual-camera", "hardware"})


@dataclass(frozen=True)
class DetectedVersions:
    runtime_version: str | None
    model_id: str | None
    model_dim: int | None


@dataclass(frozen=True)
class CompatResult:
    ok: bool
    detail: str
    detected: DetectedVersions


def _recognizer_output_dim(hef_path: str) -> int | None:
    """Read the recognizer HEF's output embedding dimension, or None on failure.

    Lazily imports ``hailo_platform`` so this module stays import-safe without
    the runtime (mock/CI).  The output vstream shape's product is the embedding
    dimension (e.g. (1, 1, 512) or (512,) both give 512).
    """
    try:  # pragma: no cover - hardware-only path
        from hailo_platform import HEF  # type: ignore[import-not-found]

        hef = HEF(hef_path)
        infos = hef.get_output_vstream_infos()
        if not infos:
            return None
        dim = 1
        for axis in tuple(infos[0].shape):
            dim *= int(axis)
        return dim
    except Exception:
        return None


def probe_hailo() -> DetectedVersions:
    """Probe the installed Hailo runtime + recognizer model; all-None if absent.

    Reports ``runtime_version`` from ``hailo_platform``, ``model_id`` from
    settings (``VISIOND_MODEL_ID``), and ``model_dim`` read from the recognizer
    HEF, so ``check_compatibility`` can verify the pinned model.  The
    hardware-absent path returns all-None (imports are guarded).
    """
    try:  # pragma: no cover - hardware-only path
        import hailo_platform  # type: ignore[import-not-found]
    except Exception:
        return DetectedVersions(runtime_version=None, model_id=None, model_dim=None)

    version = getattr(hailo_platform, "__version__", None)

    from door_visiond.settings import settings

    cfg = settings()
    model_dim = _recognizer_output_dim(str(cfg.recognizer_hef_path))
    # Only claim a model_id when we could actually read the recognizer output.
    model_id = cfg.model_id if model_dim is not None else None
    return DetectedVersions(runtime_version=version, model_id=model_id, model_dim=model_dim)


def check_compatibility(
    *,
    mode: str,
    expected_runtime: str,
    expected_model_id: str,
    expected_dim: int,
    detected: DetectedVersions | None = None,
) -> CompatResult:
    """Return whether *mode* can run on the detected hardware/runtime."""
    if mode not in _HARDWARE_MODES:
        return CompatResult(
            ok=True,
            detail=f"mode {mode!r} needs no accelerator",
            detected=DetectedVersions(None, None, None),
        )

    found = detected if detected is not None else probe_hailo()

    if found.runtime_version is None:
        return CompatResult(
            ok=False,
            detail="Hailo runtime not detected; degrading to disabled",
            detected=found,
        )
    if found.runtime_version != expected_runtime:
        return CompatResult(
            ok=False,
            detail=(
                f"Hailo runtime mismatch: expected {expected_runtime!r}, "
                f"found {found.runtime_version!r}; degrading to disabled"
            ),
            detected=found,
        )
    if found.model_id is None or found.model_dim is None:
        return CompatResult(
            ok=False,
            detail="Hailo model metadata could not be verified; degrading to disabled",
            detected=found,
        )
    if found.model_id != expected_model_id:
        return CompatResult(
            ok=False,
            detail=(f"model mismatch: expected {expected_model_id!r}, found {found.model_id!r}"),
            detected=found,
        )
    if found.model_dim != expected_dim:
        return CompatResult(
            ok=False,
            detail=f"model dim mismatch: expected {expected_dim}, found {found.model_dim}",
            detected=found,
        )
    return CompatResult(ok=True, detail="compatible", detected=found)
