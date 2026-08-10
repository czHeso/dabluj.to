"""Export format dispatch.

One table maps a format to its writer, its file extension and whether it can
carry speaker information. Adding a format (ASS, CSV) means adding an entry and
a writer, and the CLI and API both pick it up with no further changes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from dabuj.domain.speaker import Speaker
from dabuj.domain.transcript import Transcript
from dabuj.errors import ValidationError
from dabuj.export.subtitles import to_srt, to_vtt
from dabuj.export.text import to_json, to_text


class ExportFormat(str, Enum):
    """A transcript output format."""

    JSON = "json"
    SRT = "srt"
    VTT = "vtt"
    TXT = "txt"

    @property
    def extension(self) -> str:
        return _FORMATS[self].extension

    @property
    def description(self) -> str:
        return _FORMATS[self].description

    @staticmethod
    def parse(value: str) -> ExportFormat:
        """Parse a format name, accepting common aliases."""
        normalised = value.strip().lower().lstrip(".")
        aliases = {"webvtt": "vtt", "subrip": "srt", "text": "txt", "plain": "txt"}
        normalised = aliases.get(normalised, normalised)
        try:
            return ExportFormat(normalised)
        except ValueError as exc:
            supported = ", ".join(f.value for f in ExportFormat)
            raise ValidationError(
                f"{value!r} is not a supported export format.",
                reason=f"Supported formats: {supported}.",
                context={"format": value},
            ) from exc


@dataclass(frozen=True, slots=True)
class _FormatSpec:
    extension: str
    description: str
    render: Callable[..., str]
    supports_speakers: bool
    supports_translation: bool


_FORMATS: dict[ExportFormat, _FormatSpec] = {
    ExportFormat.JSON: _FormatSpec(
        extension=".json",
        description="Complete transcript including word timings and confidences",
        render=to_json,
        supports_speakers=True,
        supports_translation=True,
    ),
    ExportFormat.SRT: _FormatSpec(
        extension=".srt",
        description="SubRip subtitles",
        render=to_srt,
        supports_speakers=True,
        supports_translation=True,
    ),
    ExportFormat.VTT: _FormatSpec(
        extension=".vtt",
        description="WebVTT subtitles",
        render=to_vtt,
        supports_speakers=True,
        supports_translation=True,
    ),
    ExportFormat.TXT: _FormatSpec(
        extension=".txt",
        description="Readable plain text",
        render=to_text,
        supports_speakers=True,
        supports_translation=True,
    ),
}


def export_transcript(
    transcript: Transcript,
    export_format: ExportFormat,
    *,
    language: str | None = None,
    speakers: dict[str, Speaker] | None = None,
    include_speakers: bool = False,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Render a transcript in the requested format.

    Args:
        transcript: What to export.
        export_format: Target format.
        language: Export this translation instead of the source text.
        speakers: Speaker records, for resolving display names.
        include_speakers: Label each cue with its speaker, where the format
            supports it.
        metadata: Extra provenance, included by the JSON writer only.

    Returns:
        The rendered document.
    """
    spec = _FORMATS[export_format]

    if export_format is ExportFormat.JSON:
        return to_json(transcript, speakers=speakers, metadata=metadata)

    kwargs: dict[str, Any] = {
        "language": language,
        "speakers": speakers,
        "include_speakers": include_speakers and spec.supports_speakers,
    }
    return spec.render(transcript, **kwargs)


def write_transcript(
    transcript: Transcript,
    export_format: ExportFormat,
    destination: Path,
    **kwargs: Any,
) -> Path:
    """Render a transcript and write it to disk as UTF-8.

    Written atomically via a ``.partial`` file, so an interrupted export never
    leaves a half-written subtitle file that looks complete.

    Returns:
        ``destination``.
    """
    content = export_transcript(transcript, export_format, **kwargs)
    destination.parent.mkdir(parents=True, exist_ok=True)

    partial = destination.with_name(destination.name + ".partial")
    # newline="" keeps the writers in charge of line endings; SRT and VTT are
    # specified with LF and some players mishandle CRLF.
    with partial.open("w", encoding="utf-8", newline="") as handle:
        handle.write(content)
    partial.replace(destination)
    return destination


__all__ = ["ExportFormat", "export_transcript", "write_transcript"]
