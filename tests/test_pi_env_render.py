"""`deploy/pi-door/render-systemd-env` must hand systemd every value the template meant.

The home-layout installer used to bash-source `.env` inside ExecStart. Sourced as a shell
script, three lines of the shipped root template already misbehaved — `BIRD_KNOWN_SPECIES`
(a space and an apostrophe), `VOICE_GREETING_TEMPLATE=Hi {name}` (a brace expansion) and the
Celestrak TLE URLs (`&` forks, `FORMAT=tle` is exported as its own variable) — and any operator
value with `$(...)` would have executed as root. The renderer replaces that with a file
systemd's `EnvironmentFile=` parses; this test emulates that parser and checks every key in
both templates comes out with the literal value the author wrote.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RENDERER = REPO_ROOT / "deploy/pi-door/render-systemd-env"
TEMPLATES = {
    "root": REPO_ROOT / ".env.example",
    "pi-door": REPO_ROOT / "deploy/pi-door/doorboard.env.example",
    "enrollment-unlock": REPO_ROOT / "deploy/pi-door/enrollment-unlock.env.example",
}

_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _load_renderer():
    # No .py suffix on the script, so name the loader explicitly.
    loader = SourceFileLoader("render_systemd_env", str(RENDERER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _expected(template: Path) -> dict[str, str]:
    """What the author meant: the text after `=` minus a trailing ` # comment`, unquoted."""
    values: dict[str, str] = {}
    for line in template.read_text().splitlines():
        match = _ASSIGN.match(line)
        if not match:
            continue
        key, raw = match.group(1), match.group(2).strip()
        if raw[:1] in ("'", '"') and raw.find(raw[0], 1) != -1:
            raw = raw[1 : raw.find(raw[0], 1)]
        else:
            raw = re.sub(r"\s+#.*$", "", raw).rstrip()
        values[key] = raw
    return values


def _systemd_parse(text: str) -> dict[str, str]:
    """A small emulation of systemd's env-file parser (src/basic/env-file.c).

    Full-line `#`/`;` comments are skipped; a value starting with a quote runs to the
    matching quote; otherwise everything after `=` is the value (a `#` is NOT a comment),
    trailing whitespace trimmed, `\\x` unescaped to `x`.
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in "#;":
            continue
        key, sep, raw = stripped.partition("=")
        assert sep, f"systemd would reject: {line!r}"
        raw = raw.lstrip()
        if raw[:1] in ("'", '"'):
            closing = raw.find(raw[0], 1)
            assert closing != -1, f"unterminated quote: {line!r}"
            values[key.strip()] = raw[1:closing]
            continue
        out: list[str] = []
        escape = False
        for char in raw:
            if escape:
                out.append(char)
                escape = False
            elif char == "\\":
                escape = True
            else:
                out.append(char)
        values[key.strip()] = "".join(out).rstrip()
    return values


def test_the_templates_are_where_we_think() -> None:
    for name, path in TEMPLATES.items():
        assert path.exists(), f"{name} template moved: {path}"
    assert len(_expected(TEMPLATES["root"])) > 100


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_every_key_is_rendered_with_its_literal_value(name: str) -> None:
    renderer = _load_renderer()
    rendered = "\n".join(renderer.render(TEMPLATES[name].read_text()))
    parsed = _systemd_parse(rendered)
    expected = _expected(TEMPLATES[name])

    assert set(parsed) == set(expected), (
        f"keys differ: rendered-only={sorted(set(parsed) - set(expected))}, "
        f"missing={sorted(set(expected) - set(parsed))}"
    )
    wrong = {k: (parsed[k], expected[k]) for k in expected if parsed[k] != expected[k]}
    assert not wrong, "systemd would see a different value than the author wrote:\n" + "\n".join(
        f"  {k}: systemd={got!r} meant={meant!r}" for k, (got, meant) in wrong.items()
    )


def test_no_rendered_line_carries_a_trailing_comment() -> None:
    renderer = _load_renderer()
    for name, path in TEMPLATES.items():
        for line in renderer.render(path.read_text()):
            assert not re.search(r"\s#", line), f"{name}: {line!r}"


def test_the_three_lines_bash_got_wrong_come_out_right() -> None:
    # The audit reproduced these by sourcing the root template exactly as the old
    # ExecStart did; each is a value the shell mangled silently.
    renderer = _load_renderer()
    parsed = _systemd_parse("\n".join(renderer.render(TEMPLATES["root"].read_text())))
    assert "Anna's" in parsed["BIRD_KNOWN_SPECIES"], parsed["BIRD_KNOWN_SPECIES"]
    assert parsed["VOICE_GREETING_TEMPLATE"] == "Hi {name}"
    assert parsed["SATELLITES_TLE_URL"].endswith("GROUP=stations&FORMAT=tle")
    assert "FORMAT" not in parsed, "`&FORMAT=tle` must not become its own variable"


def test_nothing_is_evaluated() -> None:
    renderer = _load_renderer()
    parsed = _systemd_parse("\n".join(renderer.render("EVIL=$(touch /tmp/pwned) `id`\n")))
    assert parsed == {"EVIL": "$(touch /tmp/pwned) `id`"}


def test_the_script_runs_standalone_and_writes_root_only(tmp_path: Path) -> None:
    dest = tmp_path / "out" / "doorboard-home.env"
    result = subprocess.run(
        [sys.executable, str(RENDERER), str(TEMPLATES["pi-door"]), str(dest)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert dest.stat().st_mode & 0o777 == 0o600
    assert _systemd_parse(dest.read_text()) == _expected(TEMPLATES["pi-door"])
