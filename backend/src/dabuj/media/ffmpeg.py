"""The FFmpeg boundary.

Dabuj writes no codecs. Everything to do with reading, converting, mixing and
muxing media is delegated to FFmpeg, and every such call is funnelled through
this module so that the safety rules live in exactly one place:

* Commands are built as **argument arrays**. ``shell=True`` is never used, so a
  filename containing ``;`` or ``$(...)`` is inert.
* stderr is always captured and folded into a structured error, never printed
  raw at the user.
* Long operations are **cancellable**: the token terminates the child process.
* Output goes to a ``.partial`` file that is renamed only on success, so a
  cancelled or crashed run cannot leave a truncated file that later looks
  like a valid cache entry.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from dabuj.domain.media import (
    AudioStreamInfo,
    MediaInfo,
    SubtitleStreamInfo,
    VideoStreamInfo,
)
from dabuj.errors import (
    FFmpegExecutionError,
    FFmpegNotFoundError,
    UnsupportedMediaError,
    ValidationError,
)
from dabuj.logging import get_logger
from dabuj.pipeline.cancellation import CancellationToken, NullCancellationToken

logger = get_logger(__name__)

#: Sample rate expected by Whisper-family ASR models.
ASR_SAMPLE_RATE = 16_000
#: ASR models are monophonic; downmixing also roughly halves the temp file.
ASR_CHANNELS = 1

#: Cap on captured stderr, so a pathological file cannot exhaust memory.
_MAX_STDERR_CHARS = 16_000
#: Grace period between asking FFmpeg to stop and killing it.
_TERMINATE_TIMEOUT = 5.0


def find_executable(name: str, override: Path | None = None) -> Path | None:
    """Locate an FFmpeg-family binary.

    Search order: an explicit override from settings, then ``PATH``. No
    guessing at hard-coded install directories -- a wrong guess produces
    confusing version mismatches, and the config option covers the rest.

    Args:
        name: ``"ffmpeg"`` or ``"ffprobe"``.
        override: Explicit path from configuration. May be the binary itself or
            the directory containing it.
    """
    if override is not None:
        candidate = Path(override).expanduser()
        if candidate.is_dir():
            candidate = candidate / name
        # shutil.which handles the .exe suffix on Windows.
        found = shutil.which(str(candidate))
        if found:
            return Path(found)
        logger.warning("configured %s path is not executable: %s", name, candidate)

    found = shutil.which(name)
    return Path(found) if found else None


@dataclass(frozen=True, slots=True)
class AudioExtractionSpec:
    """How to convert an input's audio into something a model can consume.

    The defaults produce exactly what Whisper-family models want: 16 kHz mono
    16-bit PCM WAV. Converting once up front is far cheaper than making the
    runtime resample every chunk.
    """

    sample_rate: int = ASR_SAMPLE_RATE
    channels: int = ASR_CHANNELS
    #: Index of the audio stream to take, as reported by ffprobe.
    stream_index: int | None = None
    #: Apply EBU R128 loudness normalisation. Helps recognition on quiet or
    #: wildly inconsistent sources, at the cost of a second decode pass.
    normalize: bool = False
    #: Trim to a sub-range, in seconds. Used for previews and test fixtures.
    start: float | None = None
    duration: float | None = None

    def cache_fingerprint(self) -> str:
        """Stable string identifying this configuration, for cache keys."""
        return (
            f"sr={self.sample_rate};ch={self.channels};idx={self.stream_index};"
            f"norm={self.normalize};start={self.start};dur={self.duration}"
        )


class FFmpegService:
    """A thin, safe wrapper around the ``ffmpeg`` and ``ffprobe`` binaries."""

    def __init__(
        self,
        *,
        ffmpeg_path: Path | None = None,
        ffprobe_path: Path | None = None,
    ) -> None:
        """Resolve the binaries. Resolution is lazy-friendly: a missing binary
        is recorded as ``None`` here and only raises when actually used, so
        ``dabuj doctor`` can report on it instead of crashing at import time.
        """
        self._ffmpeg = find_executable("ffmpeg", ffmpeg_path)
        self._ffprobe = find_executable("ffprobe", ffprobe_path)

    # -- availability -----------------------------------------------------

    @property
    def is_available(self) -> bool:
        return self._ffmpeg is not None and self._ffprobe is not None

    @property
    def ffmpeg_path(self) -> Path | None:
        return self._ffmpeg

    @property
    def ffprobe_path(self) -> Path | None:
        return self._ffprobe

    def _require(self, which: str) -> Path:
        path = self._ffmpeg if which == "ffmpeg" else self._ffprobe
        if path is None:
            raise FFmpegNotFoundError(searched=[which])
        return path

    def version(self) -> str | None:
        """First line of ``ffmpeg -version``, or ``None`` if unavailable."""
        if self._ffmpeg is None:
            return None
        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [str(self._ffmpeg), "-version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        first = result.stdout.strip().splitlines()
        return first[0] if first else None

    # -- probing ----------------------------------------------------------

    def probe(self, path: Path) -> MediaInfo:
        """Inspect a media file and return what it contains.

        Raises:
            ValidationError: If the path does not point at a readable file.
            UnsupportedMediaError: If FFmpeg cannot make sense of the file.
            FFmpegNotFoundError: If ffprobe is not installed.
        """
        ffprobe = self._require("ffprobe")
        source = Path(path).expanduser()

        if not source.exists():
            raise ValidationError(
                f"The file {source.name} does not exist.",
                context={"path": str(source)},
            )
        if not source.is_file():
            raise ValidationError(
                f"{source.name} is not a file.",
                reason="Dabuj processes individual media files, not directories.",
                context={"path": str(source)},
            )

        command = [
            str(ffprobe),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            str(source),
        ]

        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                command, capture_output=True, text=True, timeout=120, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise UnsupportedMediaError(
                f"Inspecting {source.name} took too long and was stopped.",
                reason="ffprobe did not finish within 120 seconds.",
                suggestions=["The file may be corrupt or on a very slow drive"],
                context={"path": str(source)},
            ) from exc
        except OSError as exc:
            raise FFmpegExecutionError(
                "FFmpeg could not be started.",
                reason=str(exc),
                context={"path": str(source)},
            ) from exc

        if result.returncode != 0:
            raise UnsupportedMediaError(
                f"Dabuj could not read {source.name}.",
                reason=_tail(result.stderr) or "ffprobe reported an error.",
                suggestions=[
                    "Check the file plays in a normal media player",
                    "The file may be corrupt, encrypted or an unsupported format",
                ],
                context={"path": str(source), "exit_code": result.returncode},
            )

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise UnsupportedMediaError(
                f"Dabuj could not interpret the details of {source.name}.",
                reason="ffprobe returned output that was not valid JSON.",
                context={"path": str(source)},
            ) from exc

        info = _parse_probe(source, payload)

        if not info.has_audio:
            raise UnsupportedMediaError(
                f"{source.name} contains no audio track.",
                reason="Transcription needs audio, and this file has none.",
                suggestions=["Choose a file that contains speech"],
                context={"path": str(source)},
            )
        return info

    # -- audio extraction -------------------------------------------------

    def extract_audio(
        self,
        source: Path,
        destination: Path,
        *,
        spec: AudioExtractionSpec | None = None,
        total_duration: float | None = None,
        on_progress: Callable[[float], None] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> Path:
        """Extract and convert audio to a WAV file suitable for ASR.

        Writes to ``destination.partial`` and renames on success, so a
        cancelled run never leaves a file that later looks complete.

        Args:
            source: Input media.
            destination: Where the finished ``.wav`` should end up.
            spec: Conversion parameters. Defaults to 16 kHz mono, ASR-ready.
            total_duration: Source duration in seconds, used to turn FFmpeg's
                position reports into a completion fraction.
            on_progress: Called with a fraction in ``[0, 1]``.
            cancellation: Terminates FFmpeg when set.

        Returns:
            ``destination``.

        Raises:
            CancelledError: If cancellation was requested.
            FFmpegExecutionError: If FFmpeg failed.
        """
        # Fail fast with a clear message before creating any files. The actual
        # binary is resolved again inside _run_with_progress.
        self._require("ffmpeg")
        spec = spec or AudioExtractionSpec()
        token = cancellation or NullCancellationToken()
        token.raise_if_cancelled("Audio extraction")

        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".partial")
        partial.unlink(missing_ok=True)

        command = self.build_extract_command(source, partial, spec)
        logger.debug("extracting audio", extra={"stage": "audio_extract"})

        self._run_with_progress(
            command,
            total_duration=total_duration if total_duration else spec.duration,
            on_progress=on_progress,
            cancellation=token,
            description=f"Extracting audio from {source.name}",
        )

        if not partial.exists() or partial.stat().st_size == 0:
            partial.unlink(missing_ok=True)
            raise FFmpegExecutionError(
                f"No audio could be extracted from {source.name}.",
                reason="FFmpeg finished successfully but produced an empty file.",
                suggestions=["The selected audio stream may be silent or invalid"],
                context={"path": str(source)},
            )

        partial.replace(destination)
        return destination

    @staticmethod
    def build_extract_command(
        source: Path, destination: Path, spec: AudioExtractionSpec
    ) -> list[str]:
        """Build the audio-extraction argv.

        Split out from :meth:`extract_audio` so the exact flags can be unit
        tested without invoking FFmpeg. The binary name is filled in by the
        caller; this returns the arguments after it.
        """
        args: list[str] = ["-hide_banner", "-nostdin", "-y"]

        # Seeking before -i is the fast path: FFmpeg jumps rather than decoding
        # and discarding everything up to the start point.
        if spec.start is not None:
            args += ["-ss", f"{spec.start:.3f}"]

        args += ["-i", str(source)]

        if spec.duration is not None:
            args += ["-t", f"{spec.duration:.3f}"]

        # Map exactly one audio stream and nothing else.
        args += ["-map", f"0:a:{spec.stream_index}" if spec.stream_index is not None else "0:a:0"]
        args += ["-vn", "-sn", "-dn"]

        if spec.normalize:
            # EBU R128 to roughly broadcast loudness. Single-pass: the
            # two-pass variant is more accurate but doubles the decode cost for
            # a benefit ASR does not notice.
            args += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]

        args += [
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(spec.sample_rate),
            "-ac",
            str(spec.channels),
            "-f",
            "wav",
        ]
        args += ["-progress", "pipe:1", "-nostats"]
        args.append(str(destination))
        return args

    # -- process plumbing -------------------------------------------------

    def _run_with_progress(
        self,
        args: Iterable[str],
        *,
        total_duration: float | None,
        on_progress: Callable[[float], None] | None,
        cancellation: CancellationToken,
        description: str,
    ) -> None:
        """Run FFmpeg, streaming ``-progress`` output into ``on_progress``.

        stderr is drained on a background thread. Without that, a chatty FFmpeg
        fills the pipe buffer and blocks forever while we wait on stdout.
        """
        ffmpeg = self._require("ffmpeg")
        command = [str(ffmpeg), *args]

        try:
            process = subprocess.Popen(  # noqa: S603 - argv array, no shell
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise FFmpegExecutionError(
                "FFmpeg could not be started.",
                reason=str(exc),
                suggestions=["Check the ffmpeg_path setting, or reinstall FFmpeg"],
            ) from exc

        stderr_chunks: list[str] = []

        def _drain_stderr() -> None:
            if process.stderr is None:
                return
            for line in process.stderr:
                if sum(len(c) for c in stderr_chunks) < _MAX_STDERR_CHARS:
                    stderr_chunks.append(line)

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        def _terminate() -> None:
            if process.poll() is None:
                process.terminate()

        unregister = cancellation.on_cancel(_terminate)

        try:
            if process.stdout is not None:
                for line in process.stdout:
                    self._handle_progress_line(line, total_duration, on_progress)
            process.wait()
        finally:
            unregister()
            stderr_thread.join(timeout=_TERMINATE_TIMEOUT)
            if process.poll() is None:  # pragma: no cover - defensive
                process.kill()
                process.wait(timeout=_TERMINATE_TIMEOUT)

        # Cancellation is checked first: a terminated process exits non-zero,
        # and reporting that as a failure would be misleading.
        cancellation.raise_if_cancelled(description)

        if process.returncode != 0:
            raise FFmpegExecutionError(
                f"{description} failed.",
                reason=(
                    _tail("".join(stderr_chunks))
                    or f"FFmpeg exited with code {process.returncode}."
                ),
                suggestions=[
                    "Check the input file is not corrupt",
                    "Run with --debug to see the full FFmpeg output in the log",
                ],
                context={"exit_code": process.returncode},
            )

        if on_progress is not None:
            on_progress(1.0)

    @staticmethod
    def _handle_progress_line(
        line: str,
        total_duration: float | None,
        on_progress: Callable[[float], None] | None,
    ) -> None:
        """Translate one ``key=value`` line from ``-progress`` into a fraction."""
        if on_progress is None or not total_duration:
            return
        key, _, value = line.strip().partition("=")
        if key != "out_time_us":
            return
        try:
            seconds = int(value) / 1_000_000
        except ValueError:
            # FFmpeg emits "N/A" before the first frame is written.
            return
        on_progress(min(1.0, max(0.0, seconds / total_duration)))


# ---------------------------------------------------------------------------
# ffprobe JSON -> domain objects
# ---------------------------------------------------------------------------


def _tail(text: str, limit: int = 600) -> str:
    """The last ``limit`` characters, which is where FFmpeg puts the real error."""
    cleaned = (text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return "..." + cleaned[-limit:]


def _as_str(value: object) -> str | None:
    """Coerce an ffprobe field to a string, treating blanks as absent."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return None
    # ffprobe uses "N/A" and occasionally negative durations for broken files.
    return result if result >= 0 else None


def _parse_frame_rate(value: object) -> float | None:
    """Parse ffprobe's ``"30000/1001"`` rational frame rate."""
    text = str(value or "").strip()
    if not text or text in ("0/0", "N/A"):
        return None
    if "/" in text:
        numerator, _, denominator = text.partition("/")
        num, den = _as_float(numerator), _as_float(denominator)
        if num is None or not den:
            return None
        return num / den
    return _as_float(text)


def _parse_probe(path: Path, payload: dict[str, object]) -> MediaInfo:
    """Turn ffprobe's JSON into a :class:`MediaInfo`."""
    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []
    chapters = payload.get("chapters") or []
    assert isinstance(fmt, dict)
    assert isinstance(streams, list)
    assert isinstance(chapters, list)

    audio: list[AudioStreamInfo] = []
    video: list[VideoStreamInfo] = []
    subtitles: list[SubtitleStreamInfo] = []

    for stream in streams:
        if not isinstance(stream, dict):
            continue
        index = _as_int(stream.get("index")) or 0
        codec_type = str(stream.get("codec_type") or "")
        tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
        assert isinstance(tags, dict)
        language = tags.get("language")
        title = tags.get("title")

        if codec_type == "audio":
            audio.append(
                AudioStreamInfo(
                    index=index,
                    codec=_as_str(stream.get("codec_name")),
                    sample_rate=_as_int(stream.get("sample_rate")),
                    channels=_as_int(stream.get("channels")),
                    channel_layout=_as_str(stream.get("channel_layout")),
                    bit_rate=_as_int(stream.get("bit_rate")),
                    language=str(language) if language else None,
                    title=str(title) if title else None,
                    duration=_as_float(stream.get("duration")),
                )
            )
        elif codec_type == "video":
            video.append(
                VideoStreamInfo(
                    index=index,
                    codec=_as_str(stream.get("codec_name")),
                    width=_as_int(stream.get("width")),
                    height=_as_int(stream.get("height")),
                    frame_rate=_parse_frame_rate(stream.get("avg_frame_rate")),
                    bit_rate=_as_int(stream.get("bit_rate")),
                    duration=_as_float(stream.get("duration")),
                )
            )
        elif codec_type == "subtitle":
            subtitles.append(
                SubtitleStreamInfo(
                    index=index,
                    codec=_as_str(stream.get("codec_name")),
                    language=str(language) if language else None,
                    title=str(title) if title else None,
                )
            )

    duration = _as_float(fmt.get("duration"))
    if duration is None:
        # Some containers (raw streams, certain MKVs) omit the format duration;
        # fall back to the longest stream that does report one.
        candidates = [
            stream.duration
            for stream in (*audio, *video)
            if isinstance(stream, (AudioStreamInfo, VideoStreamInfo))
            and stream.duration is not None
        ]
        duration = max(candidates) if candidates else None

    size = _as_int(fmt.get("size"))
    if size is None:
        size = path.stat().st_size if path.exists() else 0

    return MediaInfo(
        path=path,
        size_bytes=size,
        format_name=_as_str(fmt.get("format_name")),
        format_long_name=_as_str(fmt.get("format_long_name")),
        duration=duration,
        bit_rate=_as_int(fmt.get("bit_rate")),
        audio_streams=tuple(audio),
        video_streams=tuple(video),
        subtitle_streams=tuple(subtitles),
        chapter_count=len(chapters),
    )


__all__ = [
    "ASR_CHANNELS",
    "ASR_SAMPLE_RATE",
    "AudioExtractionSpec",
    "FFmpegService",
    "find_executable",
]
