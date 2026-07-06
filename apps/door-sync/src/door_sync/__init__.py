"""door-sync — durable Pi → NUC/NAS upload bridge (T-502).

Public surface used by tests and the app wiring.
"""

from door_sync.engine import SyncEngine
from door_sync.fence import FenceViolation, resolve_syncable, validate_roots
from door_sync.queue import NewItem, QueueItem, QueueStats, UploadQueue
from door_sync.settings import Settings
from door_sync.targets import (
    FilesystemNasTarget,
    HttpNucTarget,
    MockMediaTarget,
    MockNucTarget,
    PermanentError,
    TransientError,
)

__all__ = [
    "FenceViolation",
    "FilesystemNasTarget",
    "HttpNucTarget",
    "MockMediaTarget",
    "MockNucTarget",
    "NewItem",
    "PermanentError",
    "QueueItem",
    "QueueStats",
    "Settings",
    "SyncEngine",
    "TransientError",
    "UploadQueue",
    "resolve_syncable",
    "validate_roots",
]
