"""Media description objects.

These mirror what ``ffprobe`` reports, normalised into something the rest of
the application can rely on. Every field that FFmpeg may legitimately omit is
optional -- a surprising number of real-world files lack a duration, a bitrate
or even a sensible channel count, and Dabuj must degrade rather than crash.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class MediaKind(str, Enum):
    """What the container turned out to hold."""

    AUDIO = "audio"
    VIDEO = "video"


class AudioStreamInfo(BaseModel):
    """One audio stream inside a container."""

    model_config = ConfigDict(frozen=True)

    index: int
    codec: str | None = None
    sample_rate: int | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, gt=0)
    channel_layout: str | None = None
    bit_rate: int | None = Field(default=None, gt=0)
    language: str | None = None
    title: str | None = None
    duration: float | None = Field(default=None, ge=0.0)


class VideoStreamInfo(BaseModel):
    """One video stream inside a container."""

    model_config = ConfigDict(frozen=True)

    index: int
    codec: str | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    frame_rate: float | None = Field(default=None, gt=0.0)
    bit_rate: int | None = Field(default=None, gt=0)
    duration: float | None = Field(default=None, ge=0.0)

    @property
    def resolution(self) -> str | None:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return None


class SubtitleStreamInfo(BaseModel):
    """One subtitle stream, preserved on export where the container allows."""

    model_config = ConfigDict(frozen=True)

    index: int
    codec: str | None = None
    language: str | None = None
    title: str | None = None


class MediaInfo(BaseModel):
    """Everything Dabuj knows about an input file after probing it."""

    model_config = ConfigDict(frozen=True)

    path: Path
    size_bytes: int = Field(ge=0)
    format_name: str | None = None
    format_long_name: str | None = None
    duration: float | None = Field(default=None, ge=0.0)
    bit_rate: int | None = Field(default=None, gt=0)

    audio_streams: tuple[AudioStreamInfo, ...] = ()
    video_streams: tuple[VideoStreamInfo, ...] = ()
    subtitle_streams: tuple[SubtitleStreamInfo, ...] = ()
    chapter_count: int = Field(default=0, ge=0)

    @property
    def kind(self) -> MediaKind:
        """Video if it carries a real video stream, else audio.

        Cover art is stored as a video stream in many audio containers, so a
        stream is only counted as video when it has more than one frame's worth
        of duration or a plausible frame rate.
        """
        return MediaKind.VIDEO if self.has_video else MediaKind.AUDIO

    @property
    def has_video(self) -> bool:
        return any(
            stream.frame_rate is not None and stream.frame_rate > 1.0
            for stream in self.video_streams
        )

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_streams)

    @property
    def primary_audio(self) -> AudioStreamInfo | None:
        """The audio stream Dabuj will transcribe by default: the first one."""
        return self.audio_streams[0] if self.audio_streams else None

    @property
    def primary_video(self) -> VideoStreamInfo | None:
        return self.video_streams[0] if self.video_streams else None

    def describe(self) -> str:
        """One-line human summary for CLI output."""
        parts: list[str] = [self.kind.value]
        if self.duration is not None:
            parts.append(format_timestamp(self.duration))
        video = self.primary_video
        if self.has_video and video is not None and video.resolution:
            parts.append(video.resolution)
        audio = self.primary_audio
        if audio is not None:
            detail = audio.codec or "audio"
            if audio.sample_rate:
                detail += f" {audio.sample_rate} Hz"
            if audio.channels:
                detail += f" {audio.channels}ch"
            parts.append(detail)
        return ", ".join(parts)


def format_timestamp(seconds: float) -> str:
    """Format seconds as ``H:MM:SS`` (or ``M:SS`` under an hour)."""
    seconds = max(0.0, seconds)
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


__all__ = [
    "AudioStreamInfo",
    "MediaInfo",
    "MediaKind",
    "SubtitleStreamInfo",
    "VideoStreamInfo",
    "format_timestamp",
]
