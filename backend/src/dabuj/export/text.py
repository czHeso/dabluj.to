"""Plain-text and JSON transcript writers."""

from __future__ import annotations

import json
from typing import Any

from dabuj.domain.media import format_timestamp
from dabuj.domain.speaker import Speaker
from dabuj.domain.transcript import Transcript
from dabuj.version import __version__


def to_text(
    transcript: Transcript,
    *,
    language: str | None = None,
    speakers: dict[str, Speaker] | None = None,
    include_timestamps: bool = True,
    include_speakers: bool = True,
) -> str:
    """Render a readable plain-text transcript.

    Consecutive segments from the same speaker are grouped under a single
    heading, which is how a person reads a transcript -- one label per turn,
    not one per sentence.
    """
    lines: list[str] = []
    previous_speaker: str | None = None

    for index, segment in enumerate(transcript.segments):
        if language is None:
            text = segment.text
        else:
            translation = segment.translation_for(language)
            text = translation.effective_text if translation else segment.text
        text = text.strip()
        if not text:
            continue

        if include_speakers and segment.speaker_id:
            if segment.speaker_id != previous_speaker:
                label = (
                    speakers[segment.speaker_id].name
                    if speakers and segment.speaker_id in speakers
                    else segment.speaker_id
                )
                if lines:
                    lines.append("")
                lines.append(f"{label}:")
            previous_speaker = segment.speaker_id

        prefix = f"[{format_timestamp(segment.start)}] " if include_timestamps else ""
        lines.append(f"{prefix}{text}")
        _ = index

    return "\n".join(lines) + ("\n" if lines else "")


def to_json(
    transcript: Transcript,
    *,
    speakers: dict[str, Speaker] | None = None,
    metadata: dict[str, Any] | None = None,
    indent: int = 2,
) -> str:
    """Render the full transcript as JSON.

    This is the lossless format: word timings, confidences, both the raw and
    edited text, translations and per-segment metadata all survive a
    round-trip. The subtitle formats necessarily discard most of that.
    """
    payload: dict[str, Any] = {
        "dabuj_version": __version__,
        "language": transcript.language,
        "language_confidence": transcript.language_confidence,
        "duration": transcript.duration,
        "segment_count": len(transcript.segments),
        "speakers": (
            [speaker.model_dump(mode="json") for speaker in speakers.values()] if speakers else []
        ),
        "segments": [
            {
                **segment.model_dump(mode="json"),
                # Derived fields, included so consumers do not have to
                # reimplement the raw/edited precedence rule.
                "text": segment.text,
                "is_edited": segment.is_edited,
                "duration": round(segment.duration, 3),
            }
            for segment in transcript.segments
        ],
    }
    if metadata:
        payload["metadata"] = metadata

    return json.dumps(payload, ensure_ascii=False, indent=indent) + "\n"


__all__ = ["to_json", "to_text"]
