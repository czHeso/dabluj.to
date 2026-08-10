"""The services the CLI and the API both call."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dabuj.application.context import AppContext
from dabuj.domain.language import Language
from dabuj.domain.quality import Device, Precision, QualityProfile
from dabuj.errors import InsufficientResourcesError, ValidationError
from dabuj.export.formats import ExportFormat, write_transcript
from dabuj.models.catalog import ModelSpec, ModelTask
from dabuj.models.download import DownloadPlan, ProgressHook
from dabuj.models.registry import InstalledModel
from dabuj.pipeline.cancellation import CancellationToken
from dabuj.pipeline.progress import ProgressReporter
from dabuj.pipeline.stages import Stage
from dabuj.pipeline.transcribe import (
    TRANSCRIPTION_STAGES,
    TranscriptionOutcome,
    TranscriptionPipeline,
    TranscriptionRequest,
)
from dabuj.projects.schema import ProjectSettings
from dabuj.projects.store import Project
from dabuj.version import __version__

#: Refuse to start a download that would leave less than this much space free.
_DISK_HEADROOM_BYTES = 2 * 1024**3


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class ProjectService:
    """Creating, listing and exporting projects."""

    def __init__(self, context: AppContext) -> None:
        self._context = context

    def create(
        self,
        source: Path,
        *,
        name: str | None = None,
        source_language: str = "auto",
        target_language: str | None = None,
        quality: QualityProfile | None = None,
        asr_model: str | None = None,
        import_media: bool = True,
    ) -> Project:
        """Create a project, validating the media before touching the disk.

        Probing first means an unreadable file produces a clear error instead
        of an empty project directory the user then has to clean up.
        """
        self._context.ffmpeg.probe(source)

        settings = ProjectSettings(
            source_language=Language.parse(source_language).code,
            target_language=Language.parse(target_language).code if target_language else None,
            quality=quality or self._context.recommendation.profile,
            device=self._context.settings.processing.device,
            precision=self._context.settings.processing.precision,
            asr_model=asr_model or self._context.settings.processing.asr_model,
            vad_filter=self._context.settings.processing.vad_filter,
        )

        return self._context.projects.create(
            source, name=name, import_media=import_media, settings=settings
        )

    def open(self, project_id: str) -> Project:
        return self._context.projects.open(project_id)

    def list(self) -> tuple[Project, ...]:
        return self._context.projects.list_projects()

    def delete(self, project_id: str, *, keep_source: bool = False) -> None:
        self._context.projects.delete(project_id, keep_source=keep_source)

    def export(
        self,
        project_id: str,
        export_format: ExportFormat,
        *,
        destination: Path | None = None,
        language: str | None = None,
        include_speakers: bool = False,
    ) -> Path:
        """Export a project's transcript.

        Args:
            project_id: Which project.
            export_format: Target format.
            destination: Where to write. Defaults to the project's exports
                directory.
            language: Export a translation rather than the source text.
            include_speakers: Label cues with speaker names.

        Raises:
            ValidationError: If the project has no transcript yet.
        """
        project = self.open(project_id)
        transcript = project.document.transcript

        if not transcript.segments:
            raise ValidationError(
                f"The project {project.name!r} has no transcript to export.",
                reason="Transcription has not been run, or produced no speech.",
                suggestions=[f"Transcribe it first: dabuj transcribe --project {project_id}"],
                context={"project_id": project_id},
            )

        target = destination or project.export_path(
            f"{_safe_stem(project.name)}{export_format.extension}"
        )

        metadata = {
            "project_id": project.id,
            "project_name": project.name,
            "dabuj_version": __version__,
            "source_filename": project.document.source.original_filename,
            "processing": project.document.processing.model_dump(mode="json"),
        }

        write_transcript(
            transcript,
            export_format,
            target,
            language=language,
            speakers=project.document.speakers,
            include_speakers=include_speakers,
            metadata=metadata,
        )

        project.document.exports[export_format.value] = str(target)
        self._context.projects.save(project)
        return target


def _safe_stem(name: str) -> str:
    """Reduce a project name to something safe for a filename."""
    cleaned = "".join(char if char.isalnum() or char in " -_." else "_" for char in name)
    return cleaned.strip().replace(" ", "_")[:80] or "transcript"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelStatus:
    """A catalog entry together with whether it is installed."""

    spec: ModelSpec
    installed: InstalledModel | None

    @property
    def is_installed(self) -> bool:
        return self.installed is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.spec.id,
            "name": self.spec.name,
            "task": self.spec.task.value,
            "provider": self.spec.provider,
            "description": self.spec.description,
            "approx_size_bytes": self.spec.approx_size_bytes,
            "approx_size_label": self.spec.approx_size_label,
            "languages": list(self.spec.languages),
            "runtime": self.spec.runtime,
            "supported_devices": [d.value for d in self.spec.supported_devices],
            "license": self.spec.license,
            "license_url": self.spec.license_url,
            "commercial_use": self.spec.commercial_use,
            "homepage": self.spec.homepage,
            "model_card": self.spec.model_card,
            "capabilities": self.spec.capabilities.model_dump(),
            "installed": self.is_installed,
            "installed_size_bytes": self.installed.size_bytes if self.installed else None,
        }


class ModelService:
    """Listing, installing and removing models."""

    def __init__(self, context: AppContext) -> None:
        self._context = context

    def list(self, task: ModelTask | None = None) -> tuple[ModelStatus, ...]:
        registry = self._context.models
        return tuple(
            ModelStatus(spec=spec, installed=registry.get(spec.id))
            for spec in registry.available(task)
        )

    def status(self, model_id: str) -> ModelStatus:
        registry = self._context.models
        spec = registry.spec_for(model_id)
        return ModelStatus(spec=spec, installed=registry.get(model_id))

    def plan_install(self, model_id: str) -> DownloadPlan:
        """Resolve exactly what will be downloaded, and check it will fit.

        Raises:
            InsufficientResourcesError: If there is not enough free disk space.
        """
        plan = self._context.models.plan_install(model_id)

        free = shutil.disk_usage(self._context.paths.models_dir).free
        if free < plan.total_bytes + _DISK_HEADROOM_BYTES:
            raise InsufficientResourcesError(
                f"There is not enough free disk space to install {plan.spec.name!r}.",
                reason=(
                    f"The download needs {_gib(plan.total_bytes)} plus headroom, "
                    f"but only {_gib(free)} is free."
                ),
                suggestions=[
                    "Free up disk space and try again",
                    "Remove unused models: dabuj models remove <id>",
                    "Point the models directory at another drive in your settings",
                ],
                context={"model_id": model_id, "required": plan.total_bytes, "free": free},
            )
        return plan

    def install(
        self,
        model_id: str,
        *,
        plan: DownloadPlan | None = None,
        on_progress: ProgressHook | None = None,
        cancellation: CancellationToken | None = None,
        force: bool = False,
    ) -> InstalledModel:
        return self._context.models.install(
            model_id,
            plan=plan,
            on_progress=on_progress,
            cancellation=cancellation,
            force=force,
        )

    def remove(self, model_id: str) -> None:
        self._context.models.remove(model_id)

    def installed_size_bytes(self) -> int:
        return self._context.models.total_size_bytes()


def _gib(value: float) -> str:
    return f"{value / 1024**3:.1f} GB"


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------


class TranscriptionService:
    """The transcription workflow, from a media file to a finished project."""

    def __init__(self, context: AppContext) -> None:
        self._context = context
        self._projects = ProjectService(context)

    @property
    def stages(self) -> tuple[Stage, ...]:
        """The stages this service's pipeline will run, in order."""
        return TRANSCRIPTION_STAGES

    def _pipeline(self) -> TranscriptionPipeline:
        return TranscriptionPipeline(
            ffmpeg=self._context.ffmpeg,
            models=self._context.models,
            store=self._context.projects,
        )

    def build_request(
        self,
        *,
        language: str | None = None,
        model_id: str | None = None,
        device: Device | None = None,
        precision: Precision | None = None,
        word_timestamps: bool | None = None,
        vad_filter: bool | None = None,
        beam_size: int | None = None,
        force: bool = False,
    ) -> TranscriptionRequest:
        """Resolve a request from explicit arguments, then settings, then defaults."""
        processing = self._context.settings.processing
        recommendation = self._context.recommendation

        return TranscriptionRequest(
            language=Language.parse(language) if language else None,
            model_id=model_id or processing.asr_model,
            device=device
            or (
                processing.device if processing.device is not Device.AUTO else recommendation.device
            ),
            precision=precision
            or (
                processing.precision
                if processing.precision is not Precision.AUTO
                else recommendation.precision
            ),
            word_timestamps=True if word_timestamps is None else word_timestamps,
            vad_filter=processing.vad_filter if vad_filter is None else vad_filter,
            beam_size=beam_size if beam_size is not None else 5,
            cpu_threads=processing.cpu_threads,
            force=force,
        )

    def transcribe_project(
        self,
        project: Project,
        request: TranscriptionRequest,
        *,
        reporter: ProgressReporter | None = None,
        cancellation: CancellationToken | None = None,
    ) -> TranscriptionOutcome:
        return self._pipeline().run(project, request, reporter=reporter, cancellation=cancellation)

    def transcribe_file(
        self,
        source: Path,
        request: TranscriptionRequest,
        *,
        name: str | None = None,
        import_media: bool = True,
        reporter: ProgressReporter | None = None,
        cancellation: CancellationToken | None = None,
    ) -> TranscriptionOutcome:
        """Create a project for ``source`` and transcribe it."""
        project = self._projects.create(
            source,
            name=name,
            source_language=request.language.code if request.language else "auto",
            asr_model=request.model_id,
            import_media=import_media,
        )
        return self.transcribe_project(
            project, request, reporter=reporter, cancellation=cancellation
        )


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One diagnostic check."""

    name: str
    ok: bool
    detail: str
    suggestion: str | None = None


class DiagnosticsService:
    """Backs ``dabuj doctor`` and ``dabuj system-info``."""

    def __init__(self, context: AppContext) -> None:
        self._context = context

    def system_report(self) -> dict[str, Any]:
        """A serialisable report, free of anything that identifies the machine."""
        context = self._context
        return {
            "application": {"name": "Dabuj", "version": __version__},
            "system": context.system.to_dict(),
            "recommendation": context.recommendation.to_dict(),
            "ffmpeg": {
                "available": context.ffmpeg.is_available,
                "version": context.ffmpeg.version(),
            },
            "storage": {
                "models_dir": str(context.paths.models_dir),
                "projects_dir": str(context.paths.projects_dir),
                "cache_dir": str(context.paths.cache_dir),
                "installed_models": len(context.models.list_installed()),
                "models_size_bytes": context.models.total_size_bytes(),
            },
            "privacy": {
                "telemetry": context.settings.privacy.telemetry,
                "cloud_providers_allowed": context.settings.privacy.allow_cloud_providers,
            },
        }

    def run_checks(self) -> tuple[CheckResult, ...]:
        """Run every start-up check and report each one independently.

        Nothing short-circuits: a user with three problems should see all three
        in one run, not discover them one at a time.
        """
        context = self._context
        results: list[CheckResult] = []

        results.append(
            CheckResult(
                name="Python runtime",
                ok=True,
                detail=f"Python {context.system.python_version} on {context.system.os_name}",
            )
        )

        config_exists = context.paths.config_file.exists()
        results.append(
            CheckResult(
                name="Configuration",
                ok=True,
                detail=(
                    str(context.paths.config_file)
                    if config_exists
                    else "Using built-in defaults (no config file yet)"
                ),
            )
        )

        write_problems = context.paths.check_writable()
        results.append(
            CheckResult(
                name="Storage directories",
                ok=not write_problems,
                detail=(
                    str(context.paths.data_dir) if not write_problems else "; ".join(write_problems)
                ),
                suggestion=(
                    "Check permissions, or set DABUJ_DATA_DIR to a writable location"
                    if write_problems
                    else None
                ),
            )
        )

        ffmpeg_version = context.ffmpeg.version()
        results.append(
            CheckResult(
                name="FFmpeg",
                ok=context.ffmpeg.is_available,
                detail=ffmpeg_version or "Not found on PATH",
                suggestion=(
                    None
                    if context.ffmpeg.is_available
                    else "Install FFmpeg, or set ffmpeg_path in your configuration"
                ),
            )
        )

        results.append(self._check_asr_runtime())

        accelerators = context.system.accelerators
        gpu = context.system.primary_gpu
        results.append(
            CheckResult(
                name="Acceleration",
                ok=True,
                detail=(
                    f"{gpu.name} via {accelerators.best_device.value}"
                    if gpu and accelerators.best_device is not Device.CPU
                    else "CPU only (no GPU acceleration detected)"
                ),
                suggestion=(
                    "This works, but transcription will be slower than real time"
                    if accelerators.best_device is Device.CPU
                    else None
                ),
            )
        )

        installed = context.models.list_installed()
        results.append(
            CheckResult(
                name="Models",
                ok=True,
                detail=(
                    f"{len(installed)} installed ({_gib(context.models.total_size_bytes())})"
                    if installed
                    else "None installed yet"
                ),
                suggestion=(
                    "Install one with: dabuj models install whisper-small"
                    if not installed
                    else None
                ),
            )
        )

        return tuple(results)

    @staticmethod
    def _check_asr_runtime() -> CheckResult:
        try:
            import faster_whisper  # noqa: PLC0415

            version = getattr(faster_whisper, "__version__", "installed")
        except ImportError:
            return CheckResult(
                name="Speech recognition runtime",
                ok=False,
                detail="faster-whisper is not installed",
                suggestion='Install it with: pip install "dabuj[asr]"',
            )
        return CheckResult(
            name="Speech recognition runtime",
            ok=True,
            detail=f"faster-whisper {version}",
        )


__all__ = [
    "CheckResult",
    "DiagnosticsService",
    "ModelService",
    "ModelStatus",
    "ProjectService",
    "TranscriptionService",
]
