"""Where Dabuj keeps things on disk, and how it refuses to leave.

Storage uses the platform's conventional application directories via
``platformdirs`` -- ``%LOCALAPPDATA%\\Dabuj`` on Windows,
``~/.local/share/dabuj`` on Linux, ``~/Library/Application Support/Dabuj`` on
macOS. Nothing is ever written into the source tree.

:func:`resolve_within` is the single choke point for turning untrusted input
into a filesystem path. Every API route and CLI command that accepts a
user-supplied path goes through it; see docs/SECURITY.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import PlatformDirs

from dabuj.errors import ConfigurationError, UnsafePathError
from dabuj.version import APP_NAME, APP_SLUG

_DIRS = PlatformDirs(appname=APP_NAME, appauthor=False, roaming=False)

#: Environment variable that relocates the whole data directory. Primarily for
#: tests and portable installs.
DATA_DIR_ENV = "DABUJ_DATA_DIR"


def resolve_within(base: Path, candidate: str | Path) -> Path:
    """Resolve ``candidate`` and prove it stays inside ``base``.

    Guards against ``../`` traversal, absolute-path injection, and symlinks
    that point outside the sandbox. Both paths are fully resolved before the
    comparison, so a symlinked ``base`` is handled correctly too.

    Args:
        base: The directory the result must remain inside.
        candidate: A relative path, usually from an HTTP request or CLI flag.

    Returns:
        The absolute, resolved path.

    Raises:
        UnsafePathError: If the result would escape ``base``.
    """
    base_resolved = base.resolve()
    # `strict=False`: the target may legitimately not exist yet (we are often
    # resolving a path we are about to create).
    target = (base_resolved / candidate).resolve()

    if target != base_resolved and base_resolved not in target.parents:
        raise UnsafePathError(
            "That path is outside the allowed directory.",
            reason="Dabuj refuses to read or write outside its own storage.",
            context={"base": str(base_resolved), "candidate": str(candidate)},
        )
    return target


def is_within(base: Path, candidate: Path) -> bool:
    """Non-raising counterpart to :func:`resolve_within`."""
    try:
        resolve_within(base, candidate)
    except UnsafePathError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class StoragePaths:
    """The set of directories the application uses.

    Every location is overridable so a user can put multi-gigabyte models and
    projects on a different drive from their system disk.
    """

    data_dir: Path
    models_dir: Path
    projects_dir: Path
    cache_dir: Path
    logs_dir: Path
    config_file: Path

    @staticmethod
    def default() -> StoragePaths:
        """Build the standard layout for this platform."""
        root_override = os.environ.get(DATA_DIR_ENV)
        root = Path(root_override).expanduser() if root_override else Path(_DIRS.user_data_dir)
        return StoragePaths.rooted_at(root)

    @staticmethod
    def rooted_at(root: Path) -> StoragePaths:
        """Build a layout with everything under a single directory."""
        root = root.expanduser()
        return StoragePaths(
            data_dir=root,
            models_dir=root / "models",
            projects_dir=root / "projects",
            cache_dir=root / "cache",
            logs_dir=root / "logs",
            config_file=root / f"{APP_SLUG}.toml",
        )

    def with_overrides(
        self,
        *,
        models_dir: Path | None = None,
        projects_dir: Path | None = None,
        cache_dir: Path | None = None,
    ) -> StoragePaths:
        """Return a copy with individual directories redirected."""
        return StoragePaths(
            data_dir=self.data_dir,
            models_dir=(models_dir or self.models_dir).expanduser(),
            projects_dir=(projects_dir or self.projects_dir).expanduser(),
            cache_dir=(cache_dir or self.cache_dir).expanduser(),
            logs_dir=self.logs_dir,
            config_file=self.config_file,
        )

    @property
    def all_directories(self) -> tuple[Path, ...]:
        return (self.data_dir, self.models_dir, self.projects_dir, self.cache_dir, self.logs_dir)

    @property
    def log_file(self) -> Path:
        return self.logs_dir / f"{APP_SLUG}.jsonl"

    def ensure(self) -> StoragePaths:
        """Create every directory, failing with a clear message if we cannot.

        Returns ``self`` so it can be chained onto ``StoragePaths.default()``.
        """
        for directory in self.all_directories:
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ConfigurationError(
                    f"Dabuj could not create the directory {directory}.",
                    reason=str(exc),
                    suggestions=[
                        "Check that the drive exists and you have permission to write to it",
                        f"Point Dabuj somewhere else by setting {DATA_DIR_ENV}",
                    ],
                    context={"directory": str(directory)},
                ) from exc
        return self

    def check_writable(self) -> list[str]:
        """Return a list of problems, empty if all directories are writable.

        Used by ``dabuj doctor``; it probes by actually writing, because
        permission bits alone are unreliable on Windows and network shares.
        """
        problems: list[str] = []
        for directory in self.all_directories:
            if not directory.exists():
                problems.append(f"{directory} does not exist")
                continue
            probe = directory / ".dabuj-write-test"
            try:
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
            except OSError as exc:
                problems.append(f"{directory} is not writable ({exc.strerror or exc})")
        return problems


__all__ = ["DATA_DIR_ENV", "StoragePaths", "is_within", "resolve_within"]
