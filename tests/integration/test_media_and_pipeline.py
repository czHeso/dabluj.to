"""Integration tests.

These run real FFmpeg against generated fixtures, and run the whole
transcription pipeline against a fake ASR provider. The fake keeps the test
fast and hermetic while still exercising the genuine orchestration: cache keys,
checkpoints, resume and cancellation.

Tests that need an actual AI model are marked ``ml`` and are not run here.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from dabuj.application.context import AppContext
from dabuj.domain.language import Language, LanguageDetection
from dabuj.domain.quality import Device, Precision
from dabuj.domain.transcript import Segment, Transcript, Word
from dabuj.errors import CancelledError, ModelNotInstalledError, UnsupportedMediaError
from dabuj.media.ffmpeg import AudioExtractionSpec, FFmpegService
from dabuj.models.registry import MARKER_FILENAME
from dabuj.pipeline.cancellation import CancellationToken
from dabuj.pipeline.progress import ProgressEvent, ProgressReporter
from dabuj.pipeline.stages import Stage
from dabuj.pipeline.transcribe import (
    TRANSCRIPTION_STAGES,
    TranscriptionPipeline,
    TranscriptionRequest,
)
from dabuj.providers.asr.base import ASROptions, ASRResult
from dabuj.providers.base import ProviderCapabilities, ProviderInfo

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# FFmpeg
# ---------------------------------------------------------------------------


class TestProbe:
    def test_probes_audio(self, ffmpeg: FFmpegService, tone_wav: Path) -> None:
        info = ffmpeg.probe(tone_wav)

        assert info.has_audio
        assert not info.has_video
        assert info.duration == pytest.approx(2.0, abs=0.2)
        assert info.primary_audio is not None
        assert info.primary_audio.sample_rate == 16000

    def test_probes_video(self, ffmpeg: FFmpegService, tone_video: Path) -> None:
        info = ffmpeg.probe(tone_video)

        assert info.has_video
        assert info.has_audio
        assert info.primary_video is not None
        assert info.primary_video.resolution == "160x120"

    def test_a_file_with_no_audio_is_rejected_clearly(
        self, ffmpeg: FFmpegService, tmp_path: Path
    ) -> None:
        import subprocess

        silent = tmp_path / "silent.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=64x64:rate=5:duration=1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(silent),
            ],
            check=True,
            capture_output=True,
        )

        with pytest.raises(UnsupportedMediaError, match="no audio track"):
            ffmpeg.probe(silent)

    def test_a_non_media_file_is_rejected(self, ffmpeg: FFmpegService, tmp_path: Path) -> None:
        junk = tmp_path / "notes.txt"
        junk.write_text("just some text", encoding="utf-8")

        with pytest.raises(UnsupportedMediaError):
            ffmpeg.probe(junk)

    def test_a_filename_with_shell_metacharacters_is_handled_literally(
        self, ffmpeg: FFmpegService, tmp_path: Path, tone_wav: Path
    ) -> None:
        """Proves the argv array defeats injection end to end."""
        nasty = tmp_path / "a; echo pwned & whoami $(id).wav"
        nasty.write_bytes(tone_wav.read_bytes())

        info = ffmpeg.probe(nasty)
        assert info.has_audio


class TestExtraction:
    def test_extracts_asr_ready_audio(
        self, ffmpeg: FFmpegService, tone_video: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "out" / "audio.wav"
        ffmpeg.extract_audio(tone_video, target, total_duration=2.0)

        info = ffmpeg.probe(target)
        assert info.primary_audio is not None
        assert info.primary_audio.sample_rate == 16000
        assert info.primary_audio.channels == 1

    def test_reports_progress(
        self, ffmpeg: FFmpegService, tone_video: Path, tmp_path: Path
    ) -> None:
        seen: list[float] = []
        ffmpeg.extract_audio(
            tone_video,
            tmp_path / "audio.wav",
            total_duration=2.0,
            on_progress=seen.append,
        )

        assert seen
        assert seen[-1] == 1.0
        assert all(0.0 <= value <= 1.0 for value in seen)

    def test_leaves_no_partial_file_on_success(
        self, ffmpeg: FFmpegService, tone_wav: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "audio.wav"
        ffmpeg.extract_audio(tone_wav, target, total_duration=2.0)

        assert target.exists()
        assert not list(tmp_path.glob("*.partial"))

    def test_cancellation_stops_and_leaves_no_output(
        self, ffmpeg: FFmpegService, tone_video: Path, tmp_path: Path
    ) -> None:
        token = CancellationToken()
        token.cancel()

        target = tmp_path / "audio.wav"
        with pytest.raises(CancelledError):
            ffmpeg.extract_audio(tone_video, target, total_duration=2.0, cancellation=token)

        assert not target.exists()

    def test_trimming_a_range(self, ffmpeg: FFmpegService, tone_wav: Path, tmp_path: Path) -> None:
        target = tmp_path / "clip.wav"
        ffmpeg.extract_audio(tone_wav, target, spec=AudioExtractionSpec(start=0.5, duration=1.0))

        info = ffmpeg.probe(target)
        assert info.duration == pytest.approx(1.0, abs=0.2)


# ---------------------------------------------------------------------------
# Pipeline, with a fake recogniser
# ---------------------------------------------------------------------------


class FakeASRProvider:
    """A deterministic stand-in for a real recogniser.

    Counts its own invocations so tests can prove that caching actually
    prevented work, rather than merely that the output looked right.
    """

    name = "fake"
    transcribe_calls = 0
    load_calls = 0

    def __init__(self, *, delay: float = 0.0) -> None:
        self._delay = delay
        self._loaded = False

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            devices=(Device.CPU,), languages=("en", "cs", "de"), word_timestamps=True
        )

    def load(
        self,
        model_path: Path,
        *,
        device: Device = Device.AUTO,
        precision: Precision = Precision.AUTO,
        cpu_threads: int = 0,
    ) -> None:
        FakeASRProvider.load_calls += 1
        self._loaded = True

    def unload(self) -> None:
        self._loaded = False

    def detect_language(self, audio_path: Path) -> LanguageDetection:
        return LanguageDetection(language=Language.parse("en"), confidence=0.99)

    def transcribe(
        self,
        audio_path: Path,
        *,
        options: ASROptions | None = None,
        on_progress=None,
        cancellation: CancellationToken | None = None,
    ) -> ASRResult:
        FakeASRProvider.transcribe_calls += 1

        # Let a cancelling thread win the race.
        if self._delay and cancellation is not None and cancellation.wait(self._delay):
            cancellation.raise_if_cancelled("Transcription")

        if cancellation is not None:
            cancellation.raise_if_cancelled("Transcription")

        if on_progress:
            on_progress(0.5, "Segment 1")
            on_progress(1.0, None)

        transcript = Transcript(
            segments=[
                Segment(
                    start=0.0,
                    end=2.0,
                    raw_text="This is a test",
                    confidence=0.9,
                    words=[Word(text="This", start=0.0, end=0.5)],
                )
            ],
            language="en",
            language_confidence=0.99,
            duration=2.0,
        )
        return ASRResult(
            transcript=transcript,
            detection=LanguageDetection(language=Language.parse("en"), confidence=0.99),
            provider=ProviderInfo(name="fake", model_id="whisper-tiny"),
            realtime_factor=10.0,
        )


@pytest.fixture
def installed_model(context: AppContext) -> str:
    """Register a fake installed model so the pipeline will proceed."""
    import json

    directory = context.paths.models_dir / "whisper-tiny"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / MARKER_FILENAME).write_text(
        json.dumps({"model_id": "whisper-tiny", "revision": "main", "size_bytes": 1}),
        encoding="utf-8",
    )
    return "whisper-tiny"


@pytest.fixture
def pipeline(context: AppContext) -> TranscriptionPipeline:
    FakeASRProvider.transcribe_calls = 0
    FakeASRProvider.load_calls = 0
    return TranscriptionPipeline(
        ffmpeg=context.ffmpeg,
        models=context.models,
        store=context.projects,
        provider_factory=FakeASRProvider,
    )


class TestTranscriptionPipeline:
    def test_runs_end_to_end(
        self,
        context: AppContext,
        pipeline: TranscriptionPipeline,
        tone_video: Path,
        installed_model: str,
    ) -> None:
        project = context.projects.create(tone_video)
        outcome = pipeline.run(project, TranscriptionRequest(model_id=installed_model))

        assert outcome.transcript.segments[0].text == "This is a test"
        assert outcome.project.document.completed_stages == TRANSCRIPTION_STAGES

    def test_a_missing_model_is_reported_before_any_work(
        self, context: AppContext, pipeline: TranscriptionPipeline, tone_wav: Path
    ) -> None:
        project = context.projects.create(tone_wav)

        with pytest.raises(ModelNotInstalledError, match="not installed"):
            pipeline.run(project, TranscriptionRequest(model_id="whisper-large-v3"))

    def test_reruns_reuse_every_stage(
        self,
        context: AppContext,
        pipeline: TranscriptionPipeline,
        tone_wav: Path,
        installed_model: str,
    ) -> None:
        project = context.projects.create(tone_wav)
        request = TranscriptionRequest(model_id=installed_model)

        pipeline.run(project, request)
        assert FakeASRProvider.transcribe_calls == 1

        reopened = context.projects.open(project.id)
        outcome = pipeline.run(reopened, request)

        assert FakeASRProvider.transcribe_calls == 1, "recognition should not have re-run"
        assert set(outcome.reused_stages) == set(TRANSCRIPTION_STAGES)
        assert outcome.transcript.segments[0].text == "This is a test"

    def test_changing_settings_reruns_only_recognition(
        self,
        context: AppContext,
        pipeline: TranscriptionPipeline,
        tone_wav: Path,
        installed_model: str,
    ) -> None:
        """The incremental-processing promise, verified against real stages."""
        project = context.projects.create(tone_wav)
        pipeline.run(project, TranscriptionRequest(model_id=installed_model, beam_size=5))

        reopened = context.projects.open(project.id)
        outcome = pipeline.run(
            reopened, TranscriptionRequest(model_id=installed_model, beam_size=1)
        )

        assert FakeASRProvider.transcribe_calls == 2
        assert Stage.MEDIA_PROBE in outcome.reused_stages
        assert Stage.AUDIO_EXTRACT in outcome.reused_stages
        assert Stage.ASR not in outcome.reused_stages

    def test_force_redoes_everything(
        self,
        context: AppContext,
        pipeline: TranscriptionPipeline,
        tone_wav: Path,
        installed_model: str,
    ) -> None:
        project = context.projects.create(tone_wav)
        request = TranscriptionRequest(model_id=installed_model)
        pipeline.run(project, request)

        reopened = context.projects.open(project.id)
        outcome = pipeline.run(reopened, TranscriptionRequest(model_id=installed_model, force=True))

        assert outcome.reused_stages == ()
        assert FakeASRProvider.transcribe_calls == 2

    def test_a_deleted_cache_file_forces_a_rerun(
        self,
        context: AppContext,
        pipeline: TranscriptionPipeline,
        tone_wav: Path,
        installed_model: str,
    ) -> None:
        """Users delete cache folders to reclaim space; that must not break resume."""
        project = context.projects.create(tone_wav)
        request = TranscriptionRequest(model_id=installed_model)
        pipeline.run(project, request)

        context.projects.clear_cache(project.id)

        reopened = context.projects.open(project.id)
        outcome = pipeline.run(reopened, request)

        assert Stage.AUDIO_EXTRACT not in outcome.reused_stages
        assert outcome.transcript.segments

    def test_progress_events_cover_every_stage(
        self,
        context: AppContext,
        pipeline: TranscriptionPipeline,
        tone_wav: Path,
        installed_model: str,
    ) -> None:
        events: list[ProgressEvent] = []
        project = context.projects.create(tone_wav)
        reporter = ProgressReporter(project.id, stages=TRANSCRIPTION_STAGES, callback=events.append)

        pipeline.run(project, TranscriptionRequest(model_id=installed_model), reporter=reporter)

        reported = {event.stage for event in events if event.stage}
        assert reported == set(TRANSCRIPTION_STAGES)
        assert events[-1].status.value == "completed"

    def test_progress_events_never_contain_transcript_text(
        self,
        context: AppContext,
        pipeline: TranscriptionPipeline,
        tone_wav: Path,
        installed_model: str,
    ) -> None:
        """Privacy: content must not leak into logs or browser consoles."""
        events: list[ProgressEvent] = []
        project = context.projects.create(tone_wav)
        reporter = ProgressReporter(project.id, stages=TRANSCRIPTION_STAGES, callback=events.append)

        pipeline.run(project, TranscriptionRequest(model_id=installed_model), reporter=reporter)

        serialised = " ".join(event.model_dump_json() for event in events)
        assert "This is a test" not in serialised

    def test_cancellation_preserves_completed_stages(
        self,
        context: AppContext,
        tone_wav: Path,
        installed_model: str,
    ) -> None:
        """Cancelling must not throw away work that was already finished."""
        FakeASRProvider.transcribe_calls = 0
        pipeline = TranscriptionPipeline(
            ffmpeg=context.ffmpeg,
            models=context.models,
            store=context.projects,
            provider_factory=lambda: FakeASRProvider(delay=5.0),
        )

        project = context.projects.create(tone_wav)
        token = CancellationToken()
        threading.Timer(0.4, token.cancel).start()

        with pytest.raises(CancelledError):
            pipeline.run(
                project,
                TranscriptionRequest(model_id=installed_model),
                cancellation=token,
            )

        reopened = context.projects.open(project.id)
        completed = reopened.document.completed_stages

        assert Stage.MEDIA_PROBE in completed
        assert Stage.AUDIO_EXTRACT in completed
        assert Stage.ASR not in completed

    def test_provider_is_unloaded_even_on_failure(
        self, context: AppContext, tone_wav: Path, installed_model: str
    ) -> None:
        """A held model would block the next queued job."""
        unloaded: list[bool] = []

        class ExplodingProvider(FakeASRProvider):
            def transcribe(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                raise RuntimeError("boom")

            def unload(self) -> None:
                unloaded.append(True)

        pipeline = TranscriptionPipeline(
            ffmpeg=context.ffmpeg,
            models=context.models,
            store=context.projects,
            provider_factory=ExplodingProvider,
        )
        project = context.projects.create(tone_wav)

        with pytest.raises(RuntimeError, match="boom"):
            pipeline.run(project, TranscriptionRequest(model_id=installed_model))

        assert unloaded == [True]

    def test_records_provenance_for_reproducibility(
        self,
        context: AppContext,
        pipeline: TranscriptionPipeline,
        tone_wav: Path,
        installed_model: str,
    ) -> None:
        project = context.projects.create(tone_wav)
        outcome = pipeline.run(project, TranscriptionRequest(model_id=installed_model))

        processing = outcome.project.document.processing
        assert processing.providers["asr"]["name"] == "fake"
        assert processing.processed_at is not None
        assert processing.ffmpeg_version
