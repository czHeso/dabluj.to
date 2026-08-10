"""End-to-end tests against a real AI model.

**Not run in CI**, and not run by a plain ``pytest``. These download model
weights and perform real inference:

    pytest -m ml

They exist because the fake-provider tests prove the *orchestration* is correct
but say nothing about whether Dabuj can actually transcribe speech. This is the
test that would catch a broken provider integration.

The speech fixture is downloaded on demand rather than committed. It is an
excerpt of John F. Kennedy's 1961 inaugural address -- a work of the United
States federal government, and therefore in the public domain -- as distributed
in the MIT-licensed faster-whisper repository for exactly this purpose.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dabuj.application.context import AppContext
from dabuj.domain.quality import Device, Precision
from dabuj.export.formats import ExportFormat, export_transcript
from dabuj.pipeline.stages import Stage
from dabuj.pipeline.transcribe import (
    TRANSCRIPTION_STAGES,
    TranscriptionPipeline,
    TranscriptionRequest,
)

pytestmark = [pytest.mark.ml, pytest.mark.slow]

_FIXTURE_URL = "https://github.com/SYSTRAN/faster-whisper/raw/master/tests/data/jfk.flac"
#: The smallest model that reliably produces this transcript.
_MODEL_ID = "whisper-tiny"


@pytest.fixture(scope="session")
def speech_sample(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A short public-domain speech clip, downloaded once per session."""
    import httpx

    from dabuj.net import create_ssl_context

    target = tmp_path_factory.mktemp("fixtures") / "jfk.flac"
    try:
        response = httpx.get(
            _FIXTURE_URL, timeout=60.0, follow_redirects=True, verify=create_ssl_context()
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - no network is a skip, not a failure
        pytest.skip(f"could not download the speech fixture: {exc}")

    target.write_bytes(response.content)
    return target


@pytest.fixture(scope="session")
def ml_context(tmp_path_factory: pytest.TempPathFactory) -> AppContext:
    """A context with a real model installed, shared across this module."""
    context = AppContext.create(data_dir=tmp_path_factory.mktemp("ml-data"))

    if not context.ffmpeg.is_available:
        pytest.skip("FFmpeg is not installed")

    try:
        context.models.install(_MODEL_ID)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"could not install {_MODEL_ID}: {exc}")

    return context


def test_transcribes_real_speech(ml_context: AppContext, speech_sample: Path) -> None:
    """The whole point: media in, correct timestamped text out."""
    project = ml_context.projects.create(speech_sample, name="JFK")
    pipeline = TranscriptionPipeline(
        ffmpeg=ml_context.ffmpeg, models=ml_context.models, store=ml_context.projects
    )

    outcome = pipeline.run(
        project,
        TranscriptionRequest(model_id=_MODEL_ID, device=Device.CPU, precision=Precision.INT8),
    )

    transcript = outcome.transcript
    assert transcript.segments, "no speech was recognised"

    # The clip is the "ask not what your country can do for you" line. Assert on
    # a distinctive phrase rather than the exact string: decoding varies subtly
    # between model sizes and runtime versions.
    text = transcript.text.lower()
    assert "ask not what your country" in text
    assert "do for your country" in text

    assert transcript.language == "en"
    assert transcript.language_confidence is not None
    assert transcript.language_confidence > 0.8

    assert outcome.project.document.completed_stages == TRANSCRIPTION_STAGES


def test_produces_word_level_timestamps(ml_context: AppContext, speech_sample: Path) -> None:
    """Dubbing depends on these, so a provider that drops them must fail here."""
    project = ml_context.projects.create(speech_sample, name="JFK words")
    pipeline = TranscriptionPipeline(
        ffmpeg=ml_context.ffmpeg, models=ml_context.models, store=ml_context.projects
    )

    outcome = pipeline.run(
        project,
        TranscriptionRequest(
            model_id=_MODEL_ID,
            device=Device.CPU,
            precision=Precision.INT8,
            word_timestamps=True,
        ),
    )

    words = [word for segment in outcome.transcript.segments for word in segment.words]
    assert len(words) > 10

    for word in words:
        assert word.end >= word.start
        assert word.text.strip()

    # Words must advance monotonically through the clip.
    starts = [word.start for word in words]
    assert starts == sorted(starts)


def test_rerun_reuses_the_cache(ml_context: AppContext, speech_sample: Path) -> None:
    """Proves resume works against real inference, not just the fake."""
    project = ml_context.projects.create(speech_sample, name="JFK cache")
    pipeline = TranscriptionPipeline(
        ffmpeg=ml_context.ffmpeg, models=ml_context.models, store=ml_context.projects
    )
    request = TranscriptionRequest(model_id=_MODEL_ID, device=Device.CPU, precision=Precision.INT8)

    first = pipeline.run(project, request)
    reopened = ml_context.projects.open(project.id)
    second = pipeline.run(reopened, request)

    assert set(second.reused_stages) == set(TRANSCRIPTION_STAGES)
    assert second.transcript.text == first.transcript.text


def test_exports_valid_subtitles(ml_context: AppContext, speech_sample: Path) -> None:
    project = ml_context.projects.create(speech_sample, name="JFK export")
    pipeline = TranscriptionPipeline(
        ffmpeg=ml_context.ffmpeg, models=ml_context.models, store=ml_context.projects
    )
    outcome = pipeline.run(
        project,
        TranscriptionRequest(model_id=_MODEL_ID, device=Device.CPU, precision=Precision.INT8),
    )

    srt = export_transcript(outcome.transcript, ExportFormat.SRT)
    assert srt.startswith("1\n")
    assert " --> " in srt
    assert "," in srt.split("\n")[1], "SRT timestamps use a comma"

    vtt = export_transcript(outcome.transcript, ExportFormat.VTT)
    assert vtt.startswith("WEBVTT")
    assert "." in vtt.split("\n")[2], "WebVTT timestamps use a period"


def test_changing_the_model_invalidates_only_recognition(
    ml_context: AppContext, speech_sample: Path
) -> None:
    """Audio extraction is expensive on a long file; it must survive a model swap."""
    project = ml_context.projects.create(speech_sample, name="JFK model swap")
    pipeline = TranscriptionPipeline(
        ffmpeg=ml_context.ffmpeg, models=ml_context.models, store=ml_context.projects
    )

    pipeline.run(
        project,
        TranscriptionRequest(
            model_id=_MODEL_ID, device=Device.CPU, precision=Precision.INT8, beam_size=5
        ),
    )

    reopened = ml_context.projects.open(project.id)
    outcome = pipeline.run(
        reopened,
        TranscriptionRequest(
            model_id=_MODEL_ID, device=Device.CPU, precision=Precision.INT8, beam_size=1
        ),
    )

    assert Stage.AUDIO_EXTRACT in outcome.reused_stages
    assert Stage.ASR not in outcome.reused_stages
