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


def probe_hailo() -> DetectedVersions:
    """Best-effort probe of the installed Hailo runtime; all-None if absent."""
    try:  # pragma: no cover - hardware-only path
        import hailo_platform  # type: ignore

        version = getattr(hailo_platform, "__version__", None)
        return DetectedVersions(runtime_version=version, model_id=None, model_dim=None)
    except Exception:
        return DetectedVersions(runtime_version=None, model_id=None, model_dim=None)


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
    if found.model_id is not None and found.model_id != expected_model_id:
        return CompatResult(
            ok=False,
            detail=(f"model mismatch: expected {expected_model_id!r}, found {found.model_id!r}"),
            detected=found,
        )
    if found.model_dim is not None and found.model_dim != expected_dim:
        return CompatResult(
            ok=False,
            detail=f"model dim mismatch: expected {expected_dim}, found {found.model_dim}",
            detected=found,
        )
    return CompatResult(ok=True, detail="compatible", detected=found)
