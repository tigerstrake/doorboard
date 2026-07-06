"""Config bundle storage: versioned `DoorConfigSettings` per door_id.

`GET /config/door/{door_id}` auto-creates a version-1 bundle from defaults on
first read so a door that's never been configured still gets something
sane. `PUT` (admin-only) bumps the version and recomputes the checksum —
callers never set either directly.
"""

from __future__ import annotations

from datetime import datetime

from doorboard_config import ConfigBundle, DoorConfigSettings, build_bundle
from sqlalchemy.orm import Session

from control_plane_api.models import DoorConfigRow


def get_or_create_bundle(session: Session, *, door_id: str, now: datetime) -> ConfigBundle:
    row = session.get(DoorConfigRow, door_id)
    if row is None:
        settings = DoorConfigSettings()
        bundle = build_bundle(door_id=door_id, version=1, settings=settings, generated_at=now)
        session.add(
            DoorConfigRow(
                door_id=door_id,
                version=1,
                settings=settings.model_dump(mode="json"),
                checksum=bundle.checksum,
                updated_at=now,
            )
        )
        session.flush()
        return bundle
    settings = DoorConfigSettings.model_validate(row.settings)
    return ConfigBundle(
        door_id=row.door_id,
        version=row.version,
        generated_at=row.updated_at,
        checksum=row.checksum,
        settings=settings,
    )


def update_bundle(
    session: Session, *, door_id: str, settings: DoorConfigSettings, now: datetime
) -> ConfigBundle:
    row = session.get(DoorConfigRow, door_id)
    next_version = row.version + 1 if row is not None else 1
    bundle = build_bundle(
        door_id=door_id, version=next_version, settings=settings, generated_at=now
    )
    if row is None:
        session.add(
            DoorConfigRow(
                door_id=door_id,
                version=next_version,
                settings=settings.model_dump(mode="json"),
                checksum=bundle.checksum,
                updated_at=now,
            )
        )
    else:
        row.version = next_version
        row.settings = settings.model_dump(mode="json")
        row.checksum = bundle.checksum
        row.updated_at = now
    session.flush()
    return bundle
