"""The model registry: what is installed, and how to install or remove it.

Installation state is recorded in a small ``dabuj-model.json`` marker written
**after** every file has been transferred and verified. That ordering is the
whole point: a directory full of half-downloaded weights has no marker, so it
is never reported as installed, and a later attempt resumes cleanly.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from dabuj.errors import ModelError, NotFoundError
from dabuj.logging import get_logger
from dabuj.models.catalog import BUILTIN_CATALOG, ModelSpec, ModelTask, find_model
from dabuj.models.download import DownloadPlan, ModelDownloader, ProgressHook
from dabuj.pipeline.cancellation import CancellationToken

logger = get_logger(__name__)

MARKER_FILENAME = "dabuj-model.json"
_MARKER_VERSION = 1


@dataclass(frozen=True, slots=True)
class InstalledModel:
    """A model present on disk."""

    model_id: str
    path: Path
    revision: str
    size_bytes: int
    installed_at: float
    #: The catalog entry, when this model is still in the catalog. A model can
    #: legitimately outlive its catalog entry after an application update.
    spec: ModelSpec | None = None

    @property
    def name(self) -> str:
        return self.spec.name if self.spec else self.model_id

    @property
    def is_orphaned(self) -> bool:
        """Installed but no longer described by the catalog."""
        return self.spec is None

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "name": self.name,
            "path": str(self.path),
            "revision": self.revision,
            "size_bytes": self.size_bytes,
            "installed_at": self.installed_at,
            "orphaned": self.is_orphaned,
        }


class ModelRegistry:
    """Tracks which catalog models are installed in a models directory."""

    def __init__(
        self,
        models_dir: Path,
        *,
        catalog: tuple[ModelSpec, ...] = BUILTIN_CATALOG,
        downloader: ModelDownloader | None = None,
    ) -> None:
        self._models_dir = models_dir
        self._catalog = catalog
        self._downloader = downloader or ModelDownloader(models_dir)

    @property
    def models_dir(self) -> Path:
        return self._models_dir

    @property
    def catalog(self) -> tuple[ModelSpec, ...]:
        return self._catalog

    # -- queries ----------------------------------------------------------

    def available(self, task: ModelTask | None = None) -> tuple[ModelSpec, ...]:
        """Catalog entries, optionally filtered by task."""
        if task is None:
            return self._catalog
        return tuple(spec for spec in self._catalog if spec.task is task)

    def spec_for(self, model_id: str) -> ModelSpec:
        """Look up a catalog entry, or explain that it does not exist."""
        spec = find_model(model_id, self._catalog)
        if spec is None:
            known = ", ".join(s.id for s in self._catalog) or "none"
            raise NotFoundError(
                f"There is no model called {model_id!r} in the catalog.",
                suggestions=[f"Available models: {known}", "See: dabuj models list"],
                context={"model_id": model_id},
            )
        return spec

    def is_installed(self, model_id: str) -> bool:
        return (self._models_dir / model_id / MARKER_FILENAME).is_file()

    def path_for(self, model_id: str) -> Path:
        """The directory an installed model lives in.

        Raises:
            NotFoundError: If the model is not installed.
        """
        directory = self._models_dir / model_id
        if not (directory / MARKER_FILENAME).is_file():
            raise NotFoundError(
                f"The model {model_id!r} is not installed.",
                suggestions=[f"Install it with: dabuj models install {model_id}"],
                context={"model_id": model_id},
            )
        return directory

    def get(self, model_id: str) -> InstalledModel | None:
        """Read the install marker, or ``None`` if not installed."""
        marker = self._models_dir / model_id / MARKER_FILENAME
        if not marker.is_file():
            return None
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("unreadable model marker", extra={"model_id": model_id})
            return None

        return InstalledModel(
            model_id=model_id,
            path=marker.parent,
            revision=str(payload.get("revision", "unknown")),
            size_bytes=int(payload.get("size_bytes", 0)),
            installed_at=float(payload.get("installed_at", 0.0)),
            spec=find_model(model_id, self._catalog),
        )

    def list_installed(self) -> tuple[InstalledModel, ...]:
        """Every installed model, including ones no longer in the catalog."""
        if not self._models_dir.is_dir():
            return ()
        installed = [
            model
            for directory in sorted(self._models_dir.iterdir())
            if directory.is_dir() and (model := self.get(directory.name)) is not None
        ]
        return tuple(installed)

    def total_size_bytes(self) -> int:
        return sum(model.size_bytes for model in self.list_installed())

    # -- installation -----------------------------------------------------

    def plan_install(self, model_id: str) -> DownloadPlan:
        """Resolve exactly what would be downloaded, without downloading it.

        Always call this before :meth:`install` and show the result: Dabuj must
        never start a multi-gigabyte transfer the user has not seen the size of.
        """
        return self._downloader.plan(self.spec_for(model_id))

    def install(
        self,
        model_id: str,
        *,
        plan: DownloadPlan | None = None,
        on_progress: ProgressHook | None = None,
        cancellation: CancellationToken | None = None,
        force: bool = False,
    ) -> InstalledModel:
        """Download and register a model.

        Args:
            model_id: Catalog ID.
            plan: A plan from :meth:`plan_install`. Resolved automatically when
                omitted, but passing the plan the user actually approved avoids
                a race where the remote contents change in between.
            on_progress: Download progress hook.
            cancellation: Cancels the transfer.
            force: Reinstall even if already present.

        Returns:
            The installed model.
        """
        spec = self.spec_for(model_id)

        existing = self.get(model_id)
        if existing is not None and not force:
            return existing

        plan = plan or self._downloader.plan(spec)
        directory = self._downloader.download(
            plan, on_progress=on_progress, cancellation=cancellation
        )

        size = sum(
            path.stat().st_size
            for path in directory.rglob("*")
            if path.is_file() and path.name != MARKER_FILENAME
        )

        # Written last: this is what makes the install atomic in practice.
        marker = {
            "marker_version": _MARKER_VERSION,
            "model_id": spec.id,
            "repo_id": spec.repo_id,
            "revision": plan.revision,
            "size_bytes": size,
            "installed_at": time.time(),
            "files": [f.path for f in plan.files],
            "license": spec.license,
        }
        (directory / MARKER_FILENAME).write_text(json.dumps(marker, indent=2), encoding="utf-8")

        logger.info("model installed", extra={"model_id": spec.id, "provider": spec.provider})
        installed = self.get(model_id)
        if installed is None:  # pragma: no cover - defensive
            raise ModelError(f"The model {model_id!r} could not be registered after download.")
        return installed

    def remove(self, model_id: str) -> None:
        """Delete an installed model and everything in its directory.

        Raises:
            NotFoundError: If the model is not installed.
            ModelError: If the files could not be deleted.
        """
        directory = self._models_dir / model_id
        if not directory.is_dir():
            raise NotFoundError(
                f"The model {model_id!r} is not installed.",
                context={"model_id": model_id},
            )

        # Remove the marker first: if deletion is interrupted, what remains is
        # correctly reported as not-installed rather than as a working model.
        (directory / MARKER_FILENAME).unlink(missing_ok=True)
        try:
            shutil.rmtree(directory)
        except OSError as exc:
            raise ModelError(
                f"The model {model_id!r} could not be fully removed.",
                reason=str(exc),
                suggestions=[
                    "Close any application that might be using the model and retry",
                    f"Or delete the folder manually: {directory}",
                ],
                context={"model_id": model_id, "path": str(directory)},
            ) from exc

        logger.info("model removed", extra={"model_id": model_id})


__all__ = ["MARKER_FILENAME", "InstalledModel", "ModelRegistry"]
