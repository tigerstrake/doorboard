"""Every enrollable light must be a light the firmware can actually play.

`PROFILE_CATALOG` is the set of profile ids an enrollee can pick, and each id is sent to the
ESP32 as its greeting effect. The ESP32's `door_effect_from_name` resolves an unknown id to
`DOOR_EFFECT_NONE`, and the controller then silently falls back to blue_wave — so a catalogue
id the firmware does not know is not an error anyone sees, just a person whose chosen colour
never appears. Four ids were exactly that fiction once (warm_amber/violet_dusk/coral_glow/
cool_white), so two-thirds of enrollees got blue whatever they picked. This pins the two
lists together against the firmware source so they cannot drift apart again.
"""

from __future__ import annotations

import re
from pathlib import Path

from door_visiond.enrollment import PROFILE_CATALOG

_EFFECTS_C = (
    Path(__file__).resolve().parents[3]
    / "firmware"
    / "esp32-door-controller"
    / "components"
    / "door_effects"
    / "door_effects.c"
)


def _firmware_effect_names() -> set[str]:
    """The names `door_effect_from_name` accepts, read straight from the firmware."""
    source = _EFFECTS_C.read_text(encoding="utf-8")
    body = source.split("door_effect_from_name", 1)[1].split("\n}", 1)[0]
    return set(re.findall(r'strcmp\(name,\s*"([^"]+)"\)', body))


def test_every_catalog_profile_is_a_real_firmware_effect() -> None:
    firmware = _firmware_effect_names()
    assert "blue_wave" in firmware, "sanity: the firmware effect table was parsed"

    catalog_ids = [profile_id for profile_id, _color in PROFILE_CATALOG]
    missing = [pid for pid in catalog_ids if pid not in firmware]
    assert not missing, (
        f"these enrollable profiles are not firmware effects, so their light silently falls "
        f"back to blue_wave: {missing}. Known effects: {sorted(firmware)}"
    )


def test_catalog_ids_are_unique() -> None:
    # profile_id is UNIQUE per person (ADR-0009 §1); a duplicated catalogue slot would make
    # the "reassign to the next free entry" logic hand out an id that is already taken.
    ids = [profile_id for profile_id, _color in PROFILE_CATALOG]
    assert len(ids) == len(set(ids)), f"duplicate profile ids in the catalogue: {ids}"
