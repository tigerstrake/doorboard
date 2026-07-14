#!/usr/bin/env python3
"""Compute "about this project" stats for the Doorboard wallboard/admin tile.

Counts lines of code by language across git-tracked files and tallies a few
structural facts (services, packages, integrations, ADRs, task briefs, contract
event types). Writes a small JSON consumed by the door-ui "About Doorboard"
surfaces (T-608). Re-run to refresh the baked numbers:

    python tools/project-stats/collect.py

Only git-tracked files are counted (so node_modules/.venv/build output never
leak in); generated and binary/lock files are excluded from the code count.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "apps" / "door-ui" / "src" / "aboutStats.json"

# Extension -> language. Only these count toward "lines of code".
CODE_LANGUAGES: dict[str, str] = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".cjs": "JavaScript",
    ".mjs": "JavaScript",
    ".c": "C/C++",
    ".cc": "C/C++",
    ".cpp": "C/C++",
    ".h": "C/C++",
    ".hpp": "C/C++",
    ".ino": "C/C++",
    ".sh": "Shell",
}

# Files/paths excluded from the code count: generated, vendored, or binary.
EXCLUDED_NAMES = {"uv.lock", "pnpm-lock.yaml", "package-lock.json"}
EXCLUDED_PREFIXES = (
    "packages/contracts/types/",  # generated from schemas
    "packages/contracts/schemas/",  # generated JSON schema
)


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def _count_lines(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return 0
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _count_dir_children(rel: str) -> int:
    d = REPO_ROOT / rel
    return sum(1 for child in d.iterdir() if child.is_dir()) if d.is_dir() else 0


def _count_glob(rel_dir: str, pattern: str, *, exclude: set[str] | None = None) -> int:
    d = REPO_ROOT / rel_dir
    if not d.is_dir():
        return 0
    exclude = exclude or set()
    return sum(1 for p in d.glob(pattern) if p.name not in exclude)


def collect() -> dict[str, object]:
    lang_lines: dict[str, int] = {}
    lang_files: dict[str, int] = {}
    total_tracked = 0

    for rel in _tracked_files():
        total_tracked += 1
        if rel in EXCLUDED_NAMES or Path(rel).name in EXCLUDED_NAMES:
            continue
        if any(rel.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
            continue
        lang = CODE_LANGUAGES.get(Path(rel).suffix)
        if lang is None:
            continue
        lines = _count_lines(REPO_ROOT / rel)
        lang_lines[lang] = lang_lines.get(lang, 0) + lines
        lang_files[lang] = lang_files.get(lang, 0) + 1

    languages = sorted(
        (
            {"name": name, "lines": lines, "files": lang_files[name]}
            for name, lines in lang_lines.items()
        ),
        key=lambda item: item["lines"],
        reverse=True,
    )

    # Task briefs: real T-<id> briefs, excluding the index and the review template.
    task_briefs = _count_glob("docs/tasks", "T-*.md", exclude={"T-x90-milestone-review.md"})
    # Contract event types: one schema file per event, minus the combined union.
    event_types = _count_glob(
        "packages/contracts/schemas",
        "*.schema.json",
        exclude={"doorboard-event.schema.json"},
    )

    return {
        "generated_at": datetime.now(UTC).date().isoformat(),
        "lines_of_code": sum(lang_lines.values()),
        "tracked_files": total_tracked,
        "languages": languages,
        "counts": {
            "services": _count_dir_children("apps"),
            "packages": _count_dir_children("packages"),
            "integrations": _count_dir_children("integrations"),
            "adrs": _count_glob("docs/adr", "[0-9][0-9][0-9][0-9]-*.md"),
            "task_briefs": task_briefs,
            "contract_event_types": event_types,
            "milestones": 8,
        },
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output JSON path (default: {DEFAULT_OUT.relative_to(REPO_ROOT)})",
    )
    parser.add_argument("--print", action="store_true", help="Also print the stats to stdout")
    args = parser.parse_args()

    stats = collect()
    args.out.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}")
    if args.print:
        print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
