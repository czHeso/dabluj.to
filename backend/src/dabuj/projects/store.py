"""Reading and writing projects on disk.

Layout of a project directory::

    <projects_dir>/<project-id>/
        project.json          manifest (see schema.py)
        source/               the imported media
        cache/                per-stage intermediate artefacts
            audio/
            asr/
            ...
        exports/              user-facing outputs

Saving is atomic: the manifest is written to ``project.json.partial`` and
renamed. A crash mid-save therefore leaves the previous, valid manifest intact
rather than a truncated one -- which for a project representing hours of
processing is the difference between a hiccup and a disaster.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from dabuj.config.paths import resolve_within
from dabuj.errors import NotFoundError, ProjectError, ValidationError
from dabuj.logging import get_logger
from dabuj.pipeline.stages import Stage
from dabuj.projects.migrations import migrate
from dabuj.projects.schema import ProjectDocument, SourceMedia, new_project_id

logger = get_logger(__name__)

MANIFEST_NAME = "project.json"
SOURCE_DIR = "source"
CACHE_DIR = "cache"
EXPORTS_DIR = "exports"

#: Cache subdirectories, one per stage that produces artefacts.
_CACHE_SUBDIRS = ("audio", "asr", "diarization", "translation", "tts", "mixing")


@dataclass(frozen=True, slots=True)
class Project:
    """An open project: its manifest plus where it lives."""

    document: ProjectDocument
    directory: Path

    @property
    def id(self) -> str:
        return self.document.id

    @property
    def name(self) -> str:
        return self.document.name

    @property
    def manifest_path(self) -> Path:
        return self.directory / MANIFEST_NAME

    @property
    def source_path(self) -> Path:
        """Absolute path to the source media."""
        return self.document.source.resolve(self.directory)

    @property
    def cache_dir(self) -> Path:
        return self.directory / CACHE_DIR

    @property
    def exports_dir(self) -> Path:
        return self.directory / EXPORTS_DIR

    def cache_path(self, category: str, filename: str) -> Path:
        """A path inside the project cache, confined to the project directory."""
        return resolve_within(self.cache_dir, f"{category}/{filename}")

    def export_path(self, filename: str) -> Path:
        """A path inside the exports directory, confined to it."""
        return resolve_within(self.exports_dir, filename)

    def relative(self, path: Path) -> str:
        """Express an absolute path inside the project as a portable relative one."""
        return PurePosixPath(path.relative_to(self.directory)).as_posix()


class ProjectStore:
    """Creates, opens, lists and deletes projects in a projects directory."""

    def __init__(self, projects_dir: Path) -> None:
        self._projects_dir = projects_dir

    @property
    def projects_dir(self) -> Path:
        return self._projects_dir

    def _directory_for(self, project_id: str) -> Path:
        """Resolve a project directory, rejecting IDs that escape the root."""
        if not project_id or "/" in project_id or "\\" in project_id:
            raise ValidationError(
                f"{project_id!r} is not a valid project ID.",
                context={"project_id": project_id},
            )
        return resolve_within(self._projects_dir, project_id)

    # -- creation ---------------------------------------------------------

    def create(
        self,
        source: Path,
        *,
        name: str | None = None,
        project_id: str | None = None,
        import_media: bool = True,
        settings: object = None,
    ) -> Project:
        """Create a project around a media file.

        Args:
            source: The media file.
            name: Display name. Defaults to the file's stem.
            project_id: Explicit ID, mainly for tests.
            import_media: Copy the media into the project. When ``False`` the
                original path is referenced in place, which avoids duplicating
                a 12 GB file but means the project breaks if it is moved.
            settings: A :class:`~dabuj.projects.schema.ProjectSettings`.

        Raises:
            ValidationError: If the source does not exist.
            ProjectError: If the project directory cannot be created.
        """
        source = Path(source).expanduser().resolve()
        if not source.is_file():
            raise ValidationError(
                f"The file {source.name} does not exist.",
                context={"path": str(source)},
            )

        identifier = project_id or new_project_id()
        directory = self._directory_for(identifier)
        if directory.exists():
            raise ProjectError(
                f"A project with the ID {identifier!r} already exists.",
                context={"project_id": identifier},
            )

        try:
            directory.mkdir(parents=True)
            (directory / SOURCE_DIR).mkdir()
            (directory / EXPORTS_DIR).mkdir()
            for sub in _CACHE_SUBDIRS:
                (directory / CACHE_DIR / sub).mkdir(parents=True)
        except OSError as exc:
            shutil.rmtree(directory, ignore_errors=True)
            raise ProjectError(
                "The project folder could not be created.",
                reason=str(exc),
                suggestions=["Check that the projects directory is writable"],
                context={"path": str(directory)},
            ) from exc

        try:
            if import_media:
                target = directory / SOURCE_DIR / source.name
                shutil.copy2(source, target)
                relative = f"{SOURCE_DIR}/{source.name}"
            else:
                target = source
                relative = source.as_posix()
        except OSError as exc:
            shutil.rmtree(directory, ignore_errors=True)
            raise ProjectError(
                "The media file could not be imported into the project.",
                reason=str(exc),
                suggestions=["Check there is enough free disk space"],
                context={"path": str(source)},
            ) from exc

        media = SourceMedia(
            relative_path=relative,
            original_filename=source.name,
            original_path=str(source),
            size_bytes=source.stat().st_size,
        )

        document = ProjectDocument(
            id=identifier,
            name=name or source.stem,
            source=media,
            **({"settings": settings} if settings is not None else {}),  # type: ignore[arg-type]
        )

        project = Project(document=document, directory=directory)
        self.save(project)
        logger.info("project created", extra={"project_id": identifier})
        return project

    # -- reading ----------------------------------------------------------

    def open(self, project_id: str) -> Project:
        """Open an existing project, migrating its schema if needed.

        Raises:
            NotFoundError: If no such project exists.
            ProjectError: If the manifest is unreadable or invalid.
            ProjectSchemaError: If the schema is too new to read.
        """
        directory = self._directory_for(project_id)
        manifest = directory / MANIFEST_NAME

        if not manifest.is_file():
            raise NotFoundError(
                f"No project with the ID {project_id!r} was found.",
                suggestions=["List your projects with: dabuj projects list"],
                context={"project_id": project_id},
            )

        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProjectError(
                f"The project file for {project_id!r} is corrupt.",
                reason=f"It is not valid JSON: {exc}",
                suggestions=["Restore the project folder from a backup if you have one"],
                context={"project_id": project_id, "path": str(manifest)},
            ) from exc
        except OSError as exc:
            raise ProjectError(
                f"The project {project_id!r} could not be read.",
                reason=str(exc),
                context={"project_id": project_id},
            ) from exc

        migrated = migrate(raw)

        try:
            document = ProjectDocument.model_validate(migrated)
        except ValueError as exc:
            raise ProjectError(
                f"The project file for {project_id!r} contains invalid data.",
                reason=str(exc),
                context={"project_id": project_id},
            ) from exc

        was_migrated = migrated.get("schema_version") != raw.get("schema_version")
        project = Project(document=document, directory=directory)
        if was_migrated:
            logger.info("project migrated", extra={"project_id": project_id})
            self.save(project)
        return project

    def exists(self, project_id: str) -> bool:
        try:
            return (self._directory_for(project_id) / MANIFEST_NAME).is_file()
        except (ValidationError, OSError):
            return False

    def list_projects(self) -> tuple[Project, ...]:
        """Every readable project, newest first.

        A project that fails to load is skipped with a warning rather than
        breaking the whole listing -- one corrupt project must not hide the
        rest.
        """
        if not self._projects_dir.is_dir():
            return ()

        projects: list[Project] = []
        for directory in self._projects_dir.iterdir():
            if not (directory / MANIFEST_NAME).is_file():
                continue
            try:
                projects.append(self.open(directory.name))
            except (ProjectError, NotFoundError) as exc:
                logger.warning(
                    "skipping unreadable project",
                    extra={"project_id": directory.name, "reason": exc.summary},
                )

        return tuple(sorted(projects, key=lambda p: p.document.updated_at, reverse=True))

    # -- writing ----------------------------------------------------------

    def save(self, project: Project) -> None:
        """Write the manifest atomically.

        Raises:
            ProjectError: If the manifest cannot be written.
        """
        project.document.updated_at = max(project.document.updated_at, project.document.created_at)
        payload = project.document.model_dump(mode="json")
        partial = project.manifest_path.with_name(MANIFEST_NAME + ".partial")

        try:
            project.directory.mkdir(parents=True, exist_ok=True)
            partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            partial.replace(project.manifest_path)
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise ProjectError(
                f"The project {project.id!r} could not be saved.",
                reason=str(exc),
                suggestions=[
                    "Check there is free disk space",
                    "Check the projects directory is writable",
                ],
                context={"project_id": project.id},
            ) from exc

    def delete(self, project_id: str, *, keep_source: bool = False) -> None:
        """Delete a project directory.

        Args:
            project_id: Which project.
            keep_source: Preserve the imported media, deleting only derived
                data. Source media is never removed without explicit intent.
        """
        directory = self._directory_for(project_id)
        if not directory.is_dir():
            raise NotFoundError(
                f"No project with the ID {project_id!r} was found.",
                context={"project_id": project_id},
            )

        try:
            if keep_source:
                for child in directory.iterdir():
                    if child.name == SOURCE_DIR:
                        continue
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
            else:
                shutil.rmtree(directory)
        except OSError as exc:
            raise ProjectError(
                f"The project {project_id!r} could not be deleted.",
                reason=str(exc),
                suggestions=["Close any application using the files and try again"],
                context={"project_id": project_id},
            ) from exc

        logger.info("project deleted", extra={"project_id": project_id})

    def clear_cache(self, project_id: str, stages: frozenset[Stage] | None = None) -> int:
        """Delete cached artefacts, returning the bytes reclaimed.

        Args:
            project_id: Which project.
            stages: Limit deletion to these stages' cache categories. ``None``
                clears everything.
        """
        directory = self._directory_for(project_id)
        cache = directory / CACHE_DIR
        if not cache.is_dir():
            return 0

        categories = (
            {_STAGE_CACHE_CATEGORY[s] for s in stages if s in _STAGE_CACHE_CATEGORY}
            if stages is not None
            else set(_CACHE_SUBDIRS)
        )

        reclaimed = 0
        for category in categories:
            target = cache / category
            if not target.is_dir():
                continue
            for path in target.rglob("*"):
                if path.is_file():
                    reclaimed += path.stat().st_size
            shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)

        return reclaimed


#: Which cache subdirectory each stage writes into.
_STAGE_CACHE_CATEGORY: dict[Stage, str] = {
    Stage.AUDIO_EXTRACT: "audio",
    Stage.ASR: "asr",
    Stage.DIARIZATION: "diarization",
    Stage.TRANSLATION: "translation",
    Stage.TTS: "tts",
    Stage.MIX: "mixing",
}


__all__ = [
    "CACHE_DIR",
    "EXPORTS_DIR",
    "MANIFEST_NAME",
    "SOURCE_DIR",
    "Project",
    "ProjectStore",
]
