"""Single source of truth for the application name and version.

Kept in its own tiny module so that anything may import it without dragging in
dependencies -- notably the CLI's ``--version`` flag and the project schema
writer, which records the version that produced a project.
"""

from __future__ import annotations

APP_NAME = "Dabuj"
"""Human-facing product name."""

APP_SLUG = "dabuj"
"""Machine-facing name: package name, CLI command, config directory."""

__version__ = "0.1.0"

PROJECT_SCHEMA_VERSION = 1
"""Version of the on-disk ``project.json`` schema.

Bump this whenever the persisted structure changes in a way that older
readers cannot understand, and add a migration in ``dabuj.projects.migrations``.
"""

__all__ = ["APP_NAME", "APP_SLUG", "PROJECT_SCHEMA_VERSION", "__version__"]
