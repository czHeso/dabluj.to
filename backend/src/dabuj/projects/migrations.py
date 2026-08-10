"""Project schema migrations.

Each migration takes the raw dict for schema version *N* and returns the dict
for *N+1*. They are chained, so opening a very old project walks it forward one
step at a time.

Rules that keep this safe:

* Migrations operate on **plain dicts**, never on the Pydantic models. Models
  describe the *current* schema; using them to read an old document would
  reinterpret it through today's defaults.
* A document from a **newer** schema is refused rather than guessed at.
* Every migration is pure, so replaying them is deterministic and testable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from dabuj.errors import ProjectSchemaError
from dabuj.version import PROJECT_SCHEMA_VERSION

#: ``{from_version: migration}``. Empty at v1 -- the first schema needs none.
#: When the schema changes, bump PROJECT_SCHEMA_VERSION and add the entry here.
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def migrate(document: dict[str, Any]) -> dict[str, Any]:
    """Bring a raw project document up to the current schema version.

    Args:
        document: The parsed contents of ``project.json``.

    Returns:
        A document at :data:`~dabuj.version.PROJECT_SCHEMA_VERSION`.

    Raises:
        ProjectSchemaError: If the version is newer than this build supports,
            or if a migration step is missing.
    """
    version = document.get("schema_version")
    if not isinstance(version, int):
        raise ProjectSchemaError(
            "This project file is missing its schema version.",
            reason="Every Dabuj project records the format version it was written with.",
            suggestions=["The file may be corrupt or may not be a Dabuj project"],
        )

    if version > PROJECT_SCHEMA_VERSION:
        raise ProjectSchemaError(
            "This project was created by a newer version of Dabuj.",
            reason=(
                f"The project uses format version {version}, but this build "
                f"understands up to version {PROJECT_SCHEMA_VERSION}."
            ),
            suggestions=["Update Dabuj to open this project"],
            context={"project_version": version, "supported_version": PROJECT_SCHEMA_VERSION},
        )

    current = dict(document)
    while version < PROJECT_SCHEMA_VERSION:
        migration = MIGRATIONS.get(version)
        if migration is None:
            raise ProjectSchemaError(
                "This project cannot be upgraded to the current format.",
                reason=f"No migration is defined from format version {version}.",
                suggestions=["This is a bug -- please report it"],
                context={"project_version": version},
            )
        current = migration(current)
        version += 1
        current["schema_version"] = version

    return current


__all__ = ["MIGRATIONS", "migrate"]
