"""Guards against source files that exist locally but never reach a clone.

This exists because of a real bug: `.gitignore` contained the unanchored
patterns ``models/`` and ``projects/``, intended for the runtime data
directories. Git applies an unanchored pattern at *every* level, so they also
matched ``backend/src/dabuj/models/`` and ``backend/src/dabuj/projects/``.

Both packages were silently dropped from every clone. The working tree was
fine, the tests passed, the build passed -- and a fresh clone died with
``ModuleNotFoundError: No module named 'dabuj.models'``.

Nothing that runs against the working tree can catch that. These tests ask git
directly what it would actually hand to someone cloning the repository.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "backend" / "src" / "dabuj"


def _git(*args: str) -> str:
    """Run git in the repository, returning stdout."""
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"git unavailable or not a repository: {result.stderr.strip()}")
    return result.stdout


@pytest.fixture(scope="module")
def tracked_files() -> frozenset[str]:
    """Every path git would give someone cloning this repository."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not running from a git working tree")
    return frozenset(_git("ls-files").splitlines())


def test_every_source_file_is_tracked(tracked_files: frozenset[str]) -> None:
    """No .py file under the package may be missing from a clone."""
    missing: list[str] = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative not in tracked_files:
            missing.append(relative)

    assert not missing, (
        "These source files exist locally but are NOT in git, so a fresh clone "
        f"would be broken: {missing}. Check .gitignore for unanchored patterns."
    )


def test_every_subpackage_is_tracked(tracked_files: frozenset[str]) -> None:
    """Each subpackage must contribute at least one tracked file.

    A stricter, more legible version of the test above: it names the package
    rather than the file, which is what the error message needs to say.
    """
    empty: list[str] = []
    for directory in sorted(PACKAGE_ROOT.iterdir()):
        if not directory.is_dir() or directory.name == "__pycache__":
            continue
        prefix = directory.relative_to(REPO_ROOT).as_posix() + "/"
        if not any(f.startswith(prefix) for f in tracked_files):
            empty.append(directory.name)

    assert not empty, (
        f"These subpackages are entirely absent from git: {empty}. "
        "A clone would fail to import them."
    )


def test_gitignore_runtime_patterns_are_anchored() -> None:
    """The specific patterns that caused the bug must stay root-anchored.

    ``models/`` and ``projects/`` name both a runtime data directory and a
    source package. Only the anchored form distinguishes them.
    """
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    lines = [
        line.strip()
        for line in gitignore.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    for dangerous in ("models/", "projects/", "cache/"):
        assert dangerous not in lines, (
            f"'{dangerous}' is unanchored and will also match "
            f"backend/src/dabuj/{dangerous}. Write '/{dangerous}' instead."
        )


def test_git_would_not_ignore_the_package(tracked_files: frozenset[str]) -> None:
    """Ask git directly whether it ignores anything inside the package."""
    _ = tracked_files  # ensures the git/repo skips have already applied

    ignored = _git(
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "--directory",
        "backend/src/dabuj/",
    ).splitlines()

    real = [path for path in ignored if "__pycache__" not in path]
    assert not real, f"git ignores these paths inside the package: {real}"
