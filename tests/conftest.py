"""Shared test fixtures.

Every test runs against a temporary data directory. Nothing here ever touches
the user's real projects, models or configuration.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from dabuj.application.context import AppContext
from dabuj.config.settings import AppSettings
from dabuj.domain.transcript import Segment, Transcript, Word
from dabuj.media.ffmpeg import FFmpegService


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """An isolated application data directory."""
    return tmp_path / "dabuj-data"


@pytest.fixture
def context(data_dir: Path) -> AppContext:
    """An AppContext rooted in a temporary directory."""
    return AppContext.create(data_dir=data_dir, settings=AppSettings())


@pytest.fixture(scope="session")
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture
def require_ffmpeg(ffmpeg_available: bool) -> None:
    if not ffmpeg_available:
        pytest.skip("FFmpeg is not installed")


@pytest.fixture
def tone_wav(tmp_path: Path, require_ffmpeg: None) -> Path:
    """A tiny synthetic WAV file.

    Generated rather than committed: it has no licensing questions, it is
    always exactly what the test expects, and it keeps the repository small.
    It contains a tone, not speech, so it exercises probing and extraction --
    not recognition quality.
    """
    target = tmp_path / "tone.wav"
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
            "sine=frequency=440:duration=2",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(target),
        ],
        check=True,
        capture_output=True,
    )
    return target


@pytest.fixture
def tone_video(tmp_path: Path, require_ffmpeg: None) -> Path:
    """A tiny synthetic video with an audio track."""
    target = tmp_path / "clip.mp4"
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
            "testsrc=size=160x120:rate=10:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(target),
        ],
        check=True,
        capture_output=True,
    )
    return target


@pytest.fixture
def ffmpeg(require_ffmpeg: None) -> FFmpegService:
    return FFmpegService()


@pytest.fixture
def sample_transcript() -> Transcript:
    """A small transcript with two speakers and word timings."""
    return Transcript(
        segments=[
            Segment(
                id="seg_a",
                start=0.0,
                end=2.5,
                raw_text="Good morning everybody",
                speaker_id="SPEAKER_00",
                confidence=0.95,
                words=[
                    Word(text="Good", start=0.0, end=0.6, confidence=0.99),
                    Word(text="morning", start=0.6, end=1.4, confidence=0.97),
                    Word(text="everybody", start=1.4, end=2.5, confidence=0.90),
                ],
            ),
            Segment(
                id="seg_b",
                start=3.0,
                end=5.75,
                raw_text="Thanks for inviting me",
                speaker_id="SPEAKER_01",
                confidence=0.42,
                words=[
                    Word(text="Thanks", start=3.0, end=3.5),
                    Word(text="for", start=3.5, end=3.8),
                    Word(text="inviting", start=3.8, end=4.9),
                    Word(text="me", start=4.9, end=5.75),
                ],
            ),
        ],
        language="en",
        language_confidence=0.98,
        duration=6.0,
    )
