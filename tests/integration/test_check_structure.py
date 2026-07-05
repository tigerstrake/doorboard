import os
import subprocess
import tempfile


def test_check_structure_success():
    # Test check-structure on a directory that is completely valid.
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a mock repo structure
        os.makedirs(os.path.join(tmpdir, "docs", "tasks"))

        # 1. Create a valid brief
        brief_content = """# T-100: Test brief

**Agent:** codex · **Milestone:** M1 · **Depends on:** T-000
Some text.

## Context
Context info.

## Deliverables
Deliverables list.

## Out of scope
Out of scope info.

## Acceptance criteria
Acceptance criteria info.
"""
        brief_path = os.path.join(tmpdir, "docs", "tasks", "T-100-test.md")
        with open(brief_path, "w", encoding="utf-8") as f:
            f.write(brief_content)

        # 2. Create another valid md file in root
        root_md_content = """# Readme
[Link to brief](docs/tasks/T-100-test.md)
"""
        root_md_path = os.path.join(tmpdir, "README.md")
        with open(root_md_path, "w", encoding="utf-8") as f:
            f.write(root_md_content)

        # Run check-structure script
        script_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "check-structure")
        )
        res = subprocess.run(["python3", script_path, tmpdir], capture_output=True, text=True)
        assert res.returncode == 0
        assert "All structure and relative link checks passed successfully!" in res.stdout


def test_check_structure_missing_section():
    # Test that check-structure catches a brief missing a section.
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "docs", "tasks"))

        # Create a brief missing "## Acceptance criteria"
        brief_content = """# T-100: Test brief

**Agent:** codex · **Milestone:** M1 · **Depends on:** T-000
Some text.

## Context
Context info.

## Deliverables
Deliverables list.

## Out of scope
Out of scope info.
"""
        brief_path = os.path.join(tmpdir, "docs", "tasks", "T-100-test.md")
        with open(brief_path, "w", encoding="utf-8") as f:
            f.write(brief_content)

        script_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "check-structure")
        )
        res = subprocess.run(["python3", script_path, tmpdir], capture_output=True, text=True)
        assert res.returncode != 0
        assert "FAIL: docs/tasks/T-100-test.md structure issues" in res.stdout
        assert "Missing section: '## Acceptance criteria'" in res.stdout


def test_check_structure_broken_link():
    # Test that check-structure catches a broken relative link.
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "docs", "tasks"))

        # Create a brief with a broken relative link
        brief_content = """# T-100: Test brief

**Agent:** codex · **Milestone:** M1 · **Depends on:** T-000
Some text.

## Context
Read [broken](../../nonexistent.md) first.

## Deliverables
Deliverables list.

## Out of scope
Out of scope info.

## Acceptance criteria
Acceptance criteria info.
"""
        brief_path = os.path.join(tmpdir, "docs", "tasks", "T-100-test.md")
        with open(brief_path, "w", encoding="utf-8") as f:
            f.write(brief_content)

        script_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "check-structure")
        )
        res = subprocess.run(["python3", script_path, tmpdir], capture_output=True, text=True)
        assert res.returncode != 0
        assert "FAIL: docs/tasks/T-100-test.md link resolution issues" in res.stdout
        assert "Broken link: '../../nonexistent.md'" in res.stdout
