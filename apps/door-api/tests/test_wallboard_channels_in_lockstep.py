"""door-api's focus allow-list must match door-ui's channel ids.

The allow-list carried a comment saying it was "kept in lockstep with the
``WallboardFocusChannel`` ids in apps/door-ui/src/wallboardChannelModel.ts" and nothing
enforced it. Adding an `about` channel to the UI and forgetting this set produced exactly
the failure the comment anticipated: the tile rendered, pressing it POSTed, door-api
returned 400 "unknown wallboard channel", and the wallboard did nothing at all — with no
error visible on either screen.

Parsing the TypeScript is deliberate. The alternative is a third list to keep in sync.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Importing door_api.app constructs its state, which needs a database path.
os.environ.setdefault("DOOR_API_DB_PATH", ":memory:")
os.environ.setdefault("DOOR_API_SOCIAL_DB_PATH", ":memory:")

from door_api.app import WALLBOARD_FOCUS_CHANNELS  # noqa: E402

CHANNEL_MODEL_TS = Path(__file__).resolve().parents[3] / "apps/door-ui/src/wallboardChannelModel.ts"


def _ui_focus_channels() -> set[str]:
    source = CHANNEL_MODEL_TS.read_text()
    match = re.search(r"export type WallboardFocusChannel\s*=\s*([^;]+);", source)
    assert match, f"WallboardFocusChannel union not found in {CHANNEL_MODEL_TS}"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def _ui_channel_definitions() -> set[str]:
    """The ids actually rendered as launcher tiles, minus the ambient reset entry."""
    source = CHANNEL_MODEL_TS.read_text()
    block = re.search(r"WALLBOARD_CHANNELS[^=]*=\s*\[(.*?)\n\];", source, re.DOTALL)
    assert block, "WALLBOARD_CHANNELS array not found"
    return set(re.findall(r'id:\s*"([a-z_]+)"', block.group(1))) - {"ambient"}


def test_door_api_accepts_every_channel_the_ui_can_request() -> None:
    missing = _ui_focus_channels() - set(WALLBOARD_FOCUS_CHANNELS)
    assert not missing, (
        f"door-ui can request {sorted(missing)} but door-api would reject it with 400; "
        "add it to WALLBOARD_FOCUS_CHANNELS"
    )


def test_door_api_accepts_no_channel_the_ui_cannot_render() -> None:
    extra = set(WALLBOARD_FOCUS_CHANNELS) - _ui_focus_channels()
    assert not extra, (
        f"door-api accepts {sorted(extra)} but door-ui has no such channel; the wallboard "
        "would be told to focus something it cannot draw"
    )


def test_every_declared_channel_has_a_launcher_tile() -> None:
    """A channel in the type union but absent from WALLBOARD_CHANNELS is unreachable."""
    assert _ui_focus_channels() == _ui_channel_definitions()


def test_about_is_focusable() -> None:
    """Guards the specific regression: About rendered but could never be focused."""
    assert "about" in WALLBOARD_FOCUS_CHANNELS
    assert "about" in _ui_focus_channels()
