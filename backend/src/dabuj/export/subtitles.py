"""SubRip (.srt) and WebVTT (.vtt) writers.

Both formats are simple, but both have details that are easy to get wrong and
that break players silently:

* SRT uses a **comma** before the milliseconds, WebVTT a **period**.
* SRT cue numbers start at **1**, not 0.
* WebVTT must begin with the literal ``WEBVTT`` header.
* Timestamps must be zero-padded to two digits (three for milliseconds), and
  hours must be present in WebVTT when any cue reaches an hour -- mixing the
  short and long forms in one file breaks some players, so Dabuj picks one form
  for the whole file.
* Blank cues are skipped: an empty subtitle flashes as a black bar in players
  that render a background.
"""

from __future__ import annotations

from dabuj.domain.speaker import Speaker
from dabuj.domain.transcript import Transcript


def _clock(seconds: float, *, separator: str, force_hours: bool = True) -> str:
    """Format seconds as ``HH:MM:SS<sep>mmm``."""
    seconds = max(0.0, seconds)
    milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)

    if force_hours or hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"
    return f"{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def srt_timestamp(seconds: float) -> str:
    """``00:01:23,450`` -- the SubRip form."""
    return _clock(seconds, separator=",")


def vtt_timestamp(seconds: float) -> str:
    """``00:01:23.450`` -- the WebVTT form."""
    return _clock(seconds, separator=".")


def _resolve_text(
    transcript: Transcript,
    segment_index: int,
    language: str | None,
) -> str:
    """The text to render for a cue: a translation when asked for, else the source."""
    segment = transcript.segments[segment_index]
    if language is None:
        return segment.text
    translation = segment.translation_for(language)
    return translation.effective_text if translation else segment.text


def _speaker_label(speaker_id: str | None, speakers: dict[str, Speaker] | None) -> str | None:
    if not speaker_id:
        return None
    if speakers and speaker_id in speakers:
        return speakers[speaker_id].name
    return speaker_id


def to_srt(
    transcript: Transcript,
    *,
    language: str | None = None,
    speakers: dict[str, Speaker] | None = None,
    include_speakers: bool = False,
) -> str:
    """Render a transcript as SubRip.

    Args:
        transcript: What to render.
        language: Render this translation instead of the source text. Segments
            without a translation fall back to the source rather than being
            dropped, so the subtitle track stays aligned with the audio.
        speakers: Used to resolve display names for speaker prefixes.
        include_speakers: Prefix each cue with ``Name: ``.

    Returns:
        The complete SRT document. Ends with a trailing newline.
    """
    blocks: list[str] = []
    cue_number = 1

    for index, segment in enumerate(transcript.segments):
        text = _resolve_text(transcript, index, language).strip()
        if not text:
            continue

        if include_speakers:
            label = _speaker_label(segment.speaker_id, speakers)
            if label:
                text = f"{label}: {text}"

        blocks.append(
            f"{cue_number}\n{srt_timestamp(segment.start)} --> {srt_timestamp(segment.end)}\n{text}"
        )
        cue_number += 1

    return "\n\n".join(blocks) + ("\n" if blocks else "")


def to_vtt(
    transcript: Transcript,
    *,
    language: str | None = None,
    speakers: dict[str, Speaker] | None = None,
    include_speakers: bool = False,
) -> str:
    """Render a transcript as WebVTT.

    Speaker names use the standard ``<v Name>`` voice span, which players can
    style, rather than being baked into the cue text.
    """
    blocks: list[str] = ["WEBVTT"]

    for index, segment in enumerate(transcript.segments):
        text = _resolve_text(transcript, index, language).strip()
        if not text:
            continue

        if include_speakers:
            label = _speaker_label(segment.speaker_id, speakers)
            if label:
                text = f"<v {label}>{text}"

        blocks.append(f"{vtt_timestamp(segment.start)} --> {vtt_timestamp(segment.end)}\n{text}")

    return "\n\n".join(blocks) + "\n"


__all__ = ["srt_timestamp", "to_srt", "to_vtt", "vtt_timestamp"]
