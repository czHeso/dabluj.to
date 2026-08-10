"""The transcription pipeline.

``media -> probe -> extract audio -> recognise -> transcript``

Every stage follows the same contract, which is what makes resume work:

1. Compute a cache key from the stage's real inputs.
2. If the project records that stage as completed **with the same key** and the
   artefact is still on disk, skip it.
3. Otherwise run it, write the artefact, and record the key.

So re-running after a crash costs only the unfinished stages, while changing
the model or the settings correctly invalidates what depended on them. The
stage code never decides for itself whether to skip -- that judgement lives in
:meth:`TranscriptionPipeline._should_skip` alone.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from dabuj.domain.language import Language, LanguageDetection
from dabuj.domain.quality import Device, Precision
from dabuj.domain.transcript import Transcript
from dabuj.errors import CancelledError, DabujError, ModelNotInstalledError
from dabuj.logging import get_logger
from dabuj.media.ffmpeg import AudioExtractionSpec, FFmpegService
from dabuj.models.registry import ModelRegistry
from dabuj.pipeline.cache import compute_cache_key, file_fingerprint
from dabuj.pipeline.cancellation import CancellationToken, NullCancellationToken
from dabuj.pipeline.progress import JobStatus, ProgressReporter
from dabuj.pipeline.stages import Stage, StageState
from dabuj.projects.schema import StageRecord
from dabuj.projects.store import Project, ProjectStore
from dabuj.providers.asr.base import ASROptions, ASRProvider
from dabuj.providers.registry import get_asr_provider

logger = get_logger(__name__)

#: Stages this pipeline runs. Diarization onward are separate milestones.
TRANSCRIPTION_STAGES: tuple[Stage, ...] = (
    Stage.MEDIA_PROBE,
    Stage.AUDIO_EXTRACT,
    Stage.ASR,
)


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    """What to transcribe and how."""

    language: Language | None = None
    model_id: str | None = None
    device: Device = Device.AUTO
    precision: Precision = Precision.AUTO
    word_timestamps: bool = True
    vad_filter: bool = True
    beam_size: int = 5
    cpu_threads: int = 0
    initial_prompt: str | None = None
    #: Ignore cached results and redo every stage.
    force: bool = False

    def to_asr_options(self) -> ASROptions:
        return ASROptions(
            language=self.language,
            word_timestamps=self.word_timestamps,
            vad_filter=self.vad_filter,
            beam_size=self.beam_size,
            initial_prompt=self.initial_prompt,
            cpu_threads=self.cpu_threads,
        )


@dataclass(slots=True)
class TranscriptionOutcome:
    """The result of a pipeline run."""

    project: Project
    transcript: Transcript
    detection: LanguageDetection | None = None
    realtime_factor: float | None = None
    warnings: tuple[str, ...] = ()
    #: Stages that were skipped because a valid cached result existed.
    reused_stages: tuple[Stage, ...] = ()


class TranscriptionPipeline:
    """Runs media through to a timestamped transcript.

    Dependencies are injected rather than constructed internally so that the
    CLI, the API and the tests all share one implementation with different
    collaborators.
    """

    def __init__(
        self,
        *,
        ffmpeg: FFmpegService,
        models: ModelRegistry,
        store: ProjectStore,
        provider_factory: type | None = None,
    ) -> None:
        self._ffmpeg = ffmpeg
        self._models = models
        self._store = store
        self._provider_factory = provider_factory

    # -- orchestration ----------------------------------------------------

    def run(
        self,
        project: Project,
        request: TranscriptionRequest,
        *,
        reporter: ProgressReporter | None = None,
        cancellation: CancellationToken | None = None,
    ) -> TranscriptionOutcome:
        """Execute the pipeline against a project.

        The project manifest is saved after every stage, so a crash loses at
        most the stage in flight.

        Raises:
            CancelledError: If cancelled. Completed stages remain valid.
            DabujError: On any handled failure; the failing stage is recorded.
        """
        token = cancellation or NullCancellationToken()
        reporter = reporter or ProgressReporter(project.id, stages=TRANSCRIPTION_STAGES)
        reused: list[Stage] = []
        warnings: list[str] = []

        reporter.job_started()

        try:
            self._stage_probe(project, request, reporter, token, reused)
            audio_path = self._stage_extract(project, request, reporter, token, reused)
            transcript, detection, realtime_factor = self._stage_asr(
                project, request, audio_path, reporter, token, reused, warnings
            )
        except CancelledError:
            reporter.job_finished(JobStatus.CANCELLED)
            self._store.save(project)
            raise
        except DabujError as exc:
            reporter.job_finished(JobStatus.FAILED, error=exc.to_payload())
            self._store.save(project)
            raise

        project.document.processing.processed_at = time.time()
        project.document.processing.ffmpeg_version = self._ffmpeg.version()
        project.document.processing.realtime_factor = realtime_factor
        for message in warnings:
            project.document.add_warning(message)

        self._store.save(project)
        reporter.job_finished(JobStatus.COMPLETED)

        return TranscriptionOutcome(
            project=project,
            transcript=transcript,
            detection=detection,
            realtime_factor=realtime_factor,
            warnings=tuple(warnings),
            reused_stages=tuple(reused),
        )

    def _should_skip(
        self, project: Project, stage: Stage, cache_key: str, artefact: Path | None
    ) -> bool:
        """Whether a stage's cached result can be reused.

        Requires all three: the recorded key matches, the stage completed, and
        the artefact still exists. The last check matters -- users delete cache
        folders to reclaim disk space, and a manifest that still claims the
        stage is done would otherwise produce a confusing failure downstream.
        """
        if not project.document.is_stage_valid(stage, cache_key):
            return False
        if artefact is not None and not artefact.exists():
            logger.info(
                "cached artefact is missing, redoing stage",
                extra={"project_id": project.id, "stage": stage.value},
            )
            return False
        return True

    @staticmethod
    def _record(
        project: Project,
        stage: Stage,
        cache_key: str,
        started: float,
        *,
        artifacts: dict[str, str] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        project.document.set_stage(
            stage,
            StageRecord(
                state=StageState.COMPLETED,
                cache_key=cache_key,
                started_at=started,
                completed_at=time.time(),
                duration_seconds=round(time.time() - started, 3),
                artifacts=artifacts or {},
                warnings=warnings or [],
            ),
        )

    # -- stage 1: probe ---------------------------------------------------

    def _stage_probe(
        self,
        project: Project,
        request: TranscriptionRequest,
        reporter: ProgressReporter,
        token: CancellationToken,
        reused: list[Stage],
    ) -> None:
        stage = Stage.MEDIA_PROBE
        source = project.source_path
        cache_key = compute_cache_key(stage, inputs={"source": file_fingerprint(source)}).value

        # The probe result lives in the manifest rather than in a cache file,
        # so reuse also requires that it actually survived the round-trip.
        if (
            not request.force
            and self._should_skip(project, stage, cache_key, None)
            and project.document.source.info is not None
        ):
            reused.append(stage)
            reporter.stage_finished(stage, state=StageState.SKIPPED)
            return

        token.raise_if_cancelled("Inspecting media")
        started = time.monotonic()
        reporter.stage_started(stage)

        info = self._ffmpeg.probe(source)
        project.document.source.info = info
        project.document.source.size_bytes = info.size_bytes

        self._record(project, stage, cache_key, started)
        self._store.save(project)
        reporter.stage_finished(stage)

    # -- stage 2: audio extraction ----------------------------------------

    def _stage_extract(
        self,
        project: Project,
        request: TranscriptionRequest,
        reporter: ProgressReporter,
        token: CancellationToken,
        reused: list[Stage],
    ) -> Path:
        stage = Stage.AUDIO_EXTRACT
        source = project.source_path
        spec = AudioExtractionSpec()

        cache_key = compute_cache_key(
            stage,
            inputs={"source": file_fingerprint(source)},
            settings={"extraction": spec.cache_fingerprint()},
        ).value

        # The key is part of the filename, so two different extraction settings
        # coexist rather than overwriting each other.
        audio_path = project.cache_path("audio", f"audio-{cache_key.split('-')[-1]}.wav")

        if not request.force and self._should_skip(project, stage, cache_key, audio_path):
            reused.append(stage)
            reporter.stage_finished(stage, state=StageState.SKIPPED)
            return audio_path

        token.raise_if_cancelled("Extracting audio")
        started = time.monotonic()
        reporter.stage_started(stage)

        info = project.document.source.info
        duration = info.duration if info else None

        self._ffmpeg.extract_audio(
            source,
            audio_path,
            spec=spec,
            total_duration=duration,
            on_progress=lambda fraction: reporter.update(stage, fraction=fraction),
            cancellation=token,
        )

        self._record(
            project,
            stage,
            cache_key,
            started,
            artifacts={"audio": project.relative(audio_path)},
        )
        self._store.save(project)
        reporter.stage_finished(stage)
        return audio_path

    # -- stage 3: recognition ---------------------------------------------

    def _resolve_model(self, request: TranscriptionRequest, project: Project) -> str:
        """Decide which ASR model to use, preferring the most explicit source."""
        model_id = request.model_id or project.document.settings.asr_model
        if model_id is None:
            from dabuj.models.catalog import ModelTask, default_model_for  # noqa: PLC0415

            spec = default_model_for(ModelTask.ASR, project.document.settings.quality)
            if spec is None:  # pragma: no cover - the catalog always has ASR entries
                raise ModelNotInstalledError("whisper-small")
            model_id = spec.id
        return model_id

    def _make_provider(self, provider_name: str) -> ASRProvider:
        if self._provider_factory is not None:
            provider: ASRProvider = self._provider_factory()
            return provider
        return get_asr_provider(provider_name)

    def _stage_asr(
        self,
        project: Project,
        request: TranscriptionRequest,
        audio_path: Path,
        reporter: ProgressReporter,
        token: CancellationToken,
        reused: list[Stage],
        warnings: list[str],
    ) -> tuple[Transcript, LanguageDetection | None, float | None]:
        stage = Stage.ASR
        model_id = self._resolve_model(request, project)
        spec = self._models.spec_for(model_id)
        options = request.to_asr_options()

        installed = self._models.get(model_id)
        if installed is None:
            raise ModelNotInstalledError(model_id)

        cache_key = compute_cache_key(
            stage,
            inputs={"audio": file_fingerprint(audio_path)},
            model_id=model_id,
            model_revision=installed.revision,
            provider=spec.provider,
            settings={"asr": options.cache_fingerprint(), "device": request.device.value},
        ).value

        transcript_cache = project.cache_path("asr", f"transcript-{cache_key.split('-')[-1]}.json")

        if not request.force and self._should_skip(project, stage, cache_key, transcript_cache):
            cached = self._load_cached_transcript(transcript_cache)
            if cached is not None:
                project.document.transcript = cached
                reused.append(stage)
                reporter.stage_finished(stage, state=StageState.SKIPPED)
                return cached, None, None

        token.raise_if_cancelled("Transcription")
        started = time.monotonic()
        reporter.stage_started(stage)

        provider = self._make_provider(spec.provider)
        provider.load(
            installed.path,
            device=request.device,
            precision=request.precision,
            cpu_threads=request.cpu_threads,
        )

        try:
            reporter.update(
                stage,
                fraction=0.0,
                message="Loading model",
                device=request.device.value,
                model_id=model_id,
            )
            result = provider.transcribe(
                audio_path,
                options=options,
                on_progress=lambda fraction, message: reporter.update(
                    stage,
                    fraction=fraction,
                    message=message,
                    device=request.device.value,
                    model_id=model_id,
                ),
                cancellation=token,
            )
        finally:
            # Release VRAM promptly; a held model blocks the next queued job.
            provider.unload()

        project.document.transcript = result.transcript
        project.document.settings.asr_model = model_id
        if result.provider is not None:
            project.document.processing.providers["asr"] = result.provider.to_dict()
            project.document.processing.device = result.provider.device
            project.document.processing.precision = result.provider.precision
        warnings.extend(result.warnings)

        self._write_cached_transcript(transcript_cache, result.transcript)
        self._record(
            project,
            stage,
            cache_key,
            started,
            artifacts={"transcript": project.relative(transcript_cache)},
            warnings=result.warnings,
        )
        self._store.save(project)
        reporter.stage_finished(stage)

        return result.transcript, result.detection, result.realtime_factor

    @staticmethod
    def _load_cached_transcript(path: Path) -> Transcript | None:
        """Read a cached transcript, treating any problem as a cache miss."""
        try:
            return Transcript.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("discarding unreadable cached transcript")
            return None

    @staticmethod
    def _write_cached_transcript(path: Path, transcript: Transcript) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".partial")
        partial.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")
        partial.replace(path)


__all__ = [
    "TRANSCRIPTION_STAGES",
    "TranscriptionOutcome",
    "TranscriptionPipeline",
    "TranscriptionRequest",
]
