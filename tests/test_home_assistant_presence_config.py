"""Guards for the Home Assistant presence bridge.

The previous version of this config was broken in three independent ways and
nothing caught any of them, because the files were "checked by hand" and never
validated against the code they call:

  1. it POSTed to /webhooks/presence, an endpoint that never existed
  2. it sent `source` in the body, which PresenceWebhookRequest forbids
  3. it sent no Authorization header, and both endpoints are admin-only

Each was fatal on its own, and all three were invisible until someone changed
their Focus and nothing happened. These tests check the YAML against the actual
FastAPI routes and the actual contract enum, so drift fails here instead.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from doorboard_contracts.events import PresenceLabel

REPO = Path(__file__).resolve().parents[1]
HA_CONFIG = REPO / "integrations/home-assistant/config"
REST_COMMANDS = HA_CONFIG / "rest_commands.yaml"
AUTOMATIONS = HA_CONFIG / "automations.yaml"
APP_PY = REPO / "apps/control-plane-api/src/control_plane_api/app.py"


class _HALoader(yaml.SafeLoader):
    """HA's custom tags (!env_var, !include, !secret) aren't plain YAML."""


for tag in ("!env_var", "!secret", "!include", "!include_dir_merge_list", "!include_dir_named"):
    _HALoader.add_constructor(tag, lambda loader, node: f"<{node.tag}:{node.value}>")


@pytest.fixture(scope="module")
def rest_commands() -> dict[str, Any]:
    return yaml.load(REST_COMMANDS.read_text(), Loader=_HALoader)


@pytest.fixture(scope="module")
def automations() -> list[dict[str, Any]]:
    return yaml.load(AUTOMATIONS.read_text(), Loader=_HALoader)


@pytest.fixture(scope="module")
def declared_routes() -> set[str]:
    """Presence webhook paths control-plane-api actually serves."""
    source = APP_PY.read_text()
    return set(re.findall(r'@app\.post\("(/status/presence/webhook/[^"]+)"\)', source))


def test_control_plane_actually_serves_two_presence_webhooks(declared_routes):
    assert declared_routes == {
        "/status/presence/webhook/focus-shortcut",
        "/status/presence/webhook/geofence-label",
    }


def test_every_rest_command_targets_a_route_that_exists(rest_commands, declared_routes):
    """The original bug: a URL nothing served."""
    assert rest_commands, "no rest_commands defined"
    for name, command in rest_commands.items():
        path = command["url"].split("8090", 1)[1]
        assert path in declared_routes, f"{name} posts to {path}, which no route serves"


def test_no_rest_command_sends_a_source_field(rest_commands):
    """The source is fixed by which endpoint is called.

    Letting the body name its own source would let a caller promote itself up the
    precedence order (manual > focus_shortcut > geofence_label > calendar >
    default) — and PresenceWebhookRequest is extra="forbid", so it is a 422 too.
    """
    for name, command in rest_commands.items():
        assert '"source"' not in command["payload"], f"{name} sends a source field"


def test_every_rest_command_authenticates(rest_commands):
    """Both endpoints are admin-authenticated."""
    for name, command in rest_commands.items():
        headers = {k.lower(): v for k, v in (command.get("headers") or {}).items()}
        assert "authorization" in headers, f"{name} sends no Authorization header"
        # Sourced from the environment, never written into the file.
        assert "env_var" in str(headers["authorization"]), (
            f"{name} does not read its token from the environment"
        )


def test_no_rest_command_sends_coordinates(rest_commands):
    """control-plane-api 422s on coordinate-shaped fields; don't send them at all."""
    banned = ("lat", "lon", "lng", "latitude", "longitude", "gps", "coord", "altitude")
    for name, command in rest_commands.items():
        payload = command["payload"].lower()
        for word in banned:
            assert word not in payload, f"{name} payload mentions {word!r}"


def test_presence_automations_are_explicitly_enabled(automations):
    """They shipped disabled, waiting on an endpoint that already existed.

    `initial_state: true` must be PRESENT, not merely not-false. HA keys an
    automation's registry entry on its `id`, so when the key is absent it restores
    the last known state — and the last known state here was off. Deleting the
    `initial_state: false` was not enough: on 2026-08-21 the automation parsed
    cleanly, logged nothing, and silently never triggered. An earlier version of
    this test accepted an absent key and so passed against that exact bug.
    """
    presence = [a for a in automations if "presence" in a["id"]]
    assert len(presence) == 2, f"expected 2 presence automations, found {len(presence)}"
    for automation in presence:
        assert "initial_state" in automation, (
            f"{automation['id']} omits initial_state, so HA will restore its previous "
            "state rather than enabling it"
        )
        assert automation["initial_state"] is True, f"{automation['id']} is disabled"


def test_presence_automations_only_call_presence_rest_commands(automations, rest_commands):
    known = {f"rest_command.{name}" for name in rest_commands}
    for automation in (a for a in automations if "presence" in a["id"]):
        for action in automation["actions"]:
            called = action.get("action")
            if called and called.startswith("rest_command."):
                assert called in known, f"{automation['id']} calls unknown {called}"


def test_zone_mapping_only_emits_labels_the_contract_accepts(automations):
    """A label outside the enum is a 422, i.e. presence silently stops updating.

    Rendering rather than pattern-matching the template: the mapping's *keys* are
    zone names and its *values* are labels, and no regex reliably tells them
    apart. Rendering asks the only question that matters — what comes out?
    """
    jinja2 = pytest.importorskip("jinja2")
    zone_automation = next(a for a in automations if a["id"] == "doorboard_presence_from_zone")
    template = jinja2.Template(zone_automation["actions"][0]["data"]["label"])
    valid = {label.value for label in PresenceLabel}

    # Every zone named in the mapping, plus one that isn't, must yield a valid label.
    named_zones = set(
        re.findall(r"'([A-Za-z_]+)':", zone_automation["actions"][0]["data"]["label"])
    )
    assert named_zones, "no zones found in the mapping"
    for zone in named_zones | {"SomewhereUnmapped"}:
        rendered = template.render(trigger={"to_state": {"state": zone}})
        assert rendered in valid, f"zone {zone!r} renders {rendered!r}, not a valid label"


def test_zone_mapping_falls_back_to_a_valid_label(automations):
    """An unmapped zone must degrade to a real label, not an empty string."""
    zone_automation = next(a for a in automations if a["id"] == "doorboard_presence_from_zone")
    template = zone_automation["actions"][0]["data"]["label"]
    assert ".get(" in template and "'away'" in template


def test_zone_label_template_renders_without_stray_whitespace(automations):
    """A folded block scalar turns newlines into spaces.

    An {% if %} ladder here rendered " available " and was rejected as an invalid
    label — the failure mode being "presence just stops working".
    """
    jinja2 = pytest.importorskip("jinja2")
    zone_automation = next(a for a in automations if a["id"] == "doorboard_presence_from_zone")
    template = jinja2.Template(zone_automation["actions"][0]["data"]["label"])
    valid = {label.value for label in PresenceLabel}
    for state in ("home", "campus", "Campus", "library", "Library", "not_home", "Somewhere"):
        rendered = template.render(trigger={"to_state": {"state": state}})
        assert rendered == rendered.strip(), f"{state} rendered {rendered!r} with whitespace"
        assert rendered in valid, f"{state} rendered {rendered!r}, not a valid label"


def test_admin_token_reaches_the_home_assistant_container():
    """The rest_commands read !env_var, so compose has to supply it.

    And it must carry the "Bearer " prefix: HA's !env_var substitutes a value
    verbatim and cannot concatenate.
    """
    for name in ("docker-compose.yml", "compose.dev.yml"):
        compose = yaml.safe_load((REPO / "infra/compose" / name).read_text())
        env = compose["services"]["home-assistant"]["environment"]
        assert "CONTROL_PLANE_ADMIN_TOKEN_HEADER" in env, f"{name} does not pass the token"
        assert str(env["CONTROL_PLANE_ADMIN_TOKEN_HEADER"]).startswith("Bearer "), (
            f"{name} token is missing the Bearer prefix"
        )


def test_rest_command_env_var_matches_what_compose_provides(rest_commands):
    """A typo either side is a silent 401."""
    compose = yaml.safe_load((REPO / "infra/compose/docker-compose.yml").read_text())
    provided = set(compose["services"]["home-assistant"]["environment"])
    for name, command in rest_commands.items():
        header = str((command.get("headers") or {}).get("authorization", ""))
        match = re.search(r"!env_var:([A-Z0-9_]+)", header)
        assert match, f"{name} authorization header is not an !env_var"
        assert match.group(1) in provided, (
            f"{name} reads {match.group(1)}, which compose does not provide"
        )
