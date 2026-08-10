"""The transcript data model.

Design rules, in order of importance:

1. **Original ASR output is never destroyed.** A segment keeps ``raw_text``
   exactly as the model produced it. User edits go to ``edited_text``, so an
   edit can always be reverted and the machine output can always be compared
   against the human one.
2. **Segment IDs are stable.** They are generated once and survive editing,
   splitting, merging, reordering and round-tripping through disk. Everything
   else -- translations, generated audio, cache entries, quality warnings --
   refers to segments by ID, so an unstable ID would silently corrupt those
   references.
3. **Translations are per-language and versioned.** Dubbing needs both the
   faithful translation and the possibly-shortened one that fits the timing,
   and the user must be able to see when those differ.

Pydantic models are used rather than plain dataclasses because this same
structure is the domain object, the persisted ``project.json`` payload and the
API schema. Three hand-maintained copies of it would drift.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dabuj.errors import ValidationError


def new_segment_id() -> str:
    """Generate a stable, collision-free segment identifier."""
    return f"seg_{uuid.uuid4().hex[:16]}"


class TranscriptSource(str, Enum):
    """Where a piece of text came from.

    Recorded per segment so the UI can show which parts a human has touched and
    so quality reporting can ignore hand-written text.
    """

    ASR = "asr"
    MANUAL = "manual"
    IMPORTED = "imported"


class Word(BaseModel):
    """A single word with its own timing.

    Word-level timestamps are optional: not every ASR provider produces them,
    and Dabuj must not pretend otherwise. When absent, the segment simply has
    an empty ``words`` list.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_ordering(self) -> Word:
        if self.end < self.start:
            raise ValueError(f"word ends before it starts: {self.start} > {self.end}")
        return self

    @property
    def duration(self) -> float:
        return self.end - self.start


class Translation(BaseModel):
    """A translation of one segment into one target language.

    Two texts are kept on purpose:

    ``text``
        The faithful translation.
    ``adapted_text``
        An optionally shortened variant produced to fit the original segment's
        duration during dubbing. ``None`` when no adaptation was needed.

    Keeping both lets the UI show what timing pressure did to the wording, and
    lets the user reject an adaptation that changed the meaning too much.
    """

    language: str
    text: str
    adapted_text: str | None = None
    source: TranscriptSource = TranscriptSource.ASR
    edited: bool = False
    provider: str | None = None
    model_id: str | None = None
    #: Estimated spoken duration of ``effective_text``, in seconds, when known.
    estimated_duration: float | None = Field(default=None, ge=0.0)

    @property
    def effective_text(self) -> str:
        """The text that should actually be spoken or displayed."""
        return self.adapted_text if self.adapted_text is not None else self.text

    @property
    def was_adapted(self) -> bool:
        return self.adapted_text is not None and self.adapted_text != self.text


class Segment(BaseModel):
    """One timed chunk of speech.

    The unit of everything downstream: translation, voice assignment, TTS
    generation and timeline placement all operate per segment.
    """

    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(default_factory=new_segment_id)
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)

    #: Verbatim ASR output. Never overwritten by user edits.
    raw_text: str = ""
    #: User-supplied replacement. ``None`` means "not edited".
    edited_text: str | None = None

    speaker_id: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    words: list[Word] = Field(default_factory=list)
    translations: dict[str, Translation] = Field(default_factory=dict)
    source: TranscriptSource = TranscriptSource.ASR

    #: Free-form per-segment notes, e.g. ``{"no_speech_prob": 0.02}``.
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("raw_text", "edited_text")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value if value is None else value.strip()

    @model_validator(mode="after")
    def _check_ordering(self) -> Segment:
        if self.end < self.start:
            raise ValueError(f"segment ends before it starts: {self.start} > {self.end}")
        return self

    # -- derived ----------------------------------------------------------

    @property
    def text(self) -> str:
        """The text to display and translate: the edit if there is one."""
        return self.edited_text if self.edited_text is not None else self.raw_text

    @property
    def is_edited(self) -> bool:
        return self.edited_text is not None and self.edited_text != self.raw_text

    @property
    def duration(self) -> float:
        return self.end - self.start

    def translation_for(self, language: str) -> Translation | None:
        return self.translations.get(language)

    # -- editing ----------------------------------------------------------

    def with_edit(self, text: str) -> Segment:
        """Return a copy whose text has been edited, preserving ``raw_text``.

        Setting the text back to exactly the ASR output clears the edit rather
        than recording a no-op change.
        """
        stripped = text.strip()
        return self.model_copy(
            update={"edited_text": None if stripped == self.raw_text else stripped}
        )

    def revert_edit(self) -> Segment:
        """Return a copy with any manual edit discarded."""
        return self.model_copy(update={"edited_text": None})

    def split_at(self, timestamp: float) -> tuple[Segment, Segment]:
        """Split into two segments at ``timestamp``.

        Word timings decide where the text is divided when they are available;
        otherwise the text is split proportionally on a word boundary, which is
        approximate but never loses characters.

        The left half keeps this segment's ID so existing references stay
        valid; the right half gets a fresh one.

        Raises:
            ValidationError: If ``timestamp`` is not strictly inside the segment.
        """
        if not (self.start < timestamp < self.end):
            raise ValidationError(
                "The split point must lie inside the segment.",
                reason=(
                    f"Requested {timestamp:.3f}s, but the segment spans "
                    f"{self.start:.3f}s to {self.end:.3f}s."
                ),
                context={"segment_id": self.id},
            )

        if self.words:
            left_words = [w for w in self.words if w.start < timestamp]
            right_words = [w for w in self.words if w.start >= timestamp]
            left_text = " ".join(w.text.strip() for w in left_words).strip()
            right_text = " ".join(w.text.strip() for w in right_words).strip()
        else:
            left_words, right_words = [], []
            left_text, right_text = _split_text_proportionally(
                self.text, (timestamp - self.start) / self.duration if self.duration else 0.5
            )

        # Editing then splitting must not resurrect the pre-edit text, so the
        # halves of an edited segment are themselves marked as edited.
        edited = self.is_edited
        left = self.model_copy(
            update={
                "end": timestamp,
                "raw_text": left_text if not edited else self.raw_text,
                "edited_text": left_text if edited else None,
                "words": left_words,
                # Translations no longer describe either half.
                "translations": {},
            }
        )
        right = self.model_copy(
            update={
                "id": new_segment_id(),
                "start": timestamp,
                "raw_text": right_text if not edited else "",
                "edited_text": right_text if edited else None,
                "words": right_words,
                "translations": {},
            }
        )
        return left, right

    def merged_with(self, other: Segment) -> Segment:
        """Return a single segment spanning this one and ``other``.

        ``other`` must start at or after this segment. The result keeps this
        segment's ID and speaker; translations are dropped because a merged
        segment needs retranslating.
        """
        if other.start < self.start:
            raise ValidationError(
                "Segments must be merged in chronological order.",
                context={"segment_id": self.id, "other_id": other.id},
            )

        joined_raw = " ".join(part for part in (self.raw_text, other.raw_text) if part).strip()
        edited = self.is_edited or other.is_edited
        joined_edited = (
            " ".join(part for part in (self.text, other.text) if part).strip() if edited else None
        )

        confidences = [c for c in (self.confidence, other.confidence) if c is not None]
        return self.model_copy(
            update={
                "end": max(self.end, other.end),
                "raw_text": joined_raw,
                "edited_text": joined_edited,
                "words": [*self.words, *other.words],
                "confidence": sum(confidences) / len(confidences) if confidences else None,
                "translations": {},
            }
        )


def _split_text_proportionally(text: str, ratio: float) -> tuple[str, str]:
    """Split ``text`` near ``ratio`` of its length, on a word boundary.

    Used only when word timings are unavailable. Guarantees that concatenating
    the halves reproduces the original words in order.
    """
    words = text.split()
    if not words:
        return "", ""
    if len(words) == 1:
        return (words[0], "") if ratio >= 0.5 else ("", words[0])

    index = max(1, min(len(words) - 1, round(len(words) * ratio)))
    return " ".join(words[:index]), " ".join(words[index:])


class Transcript(BaseModel):
    """An ordered collection of segments plus what produced them."""

    model_config = ConfigDict(validate_assignment=True)

    segments: list[Segment] = Field(default_factory=list)
    #: Detected or user-specified source language code.
    language: str | None = None
    language_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    #: Total audio duration in seconds, for progress and coverage reporting.
    duration: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _check_unique_ids(self) -> Transcript:
        seen: set[str] = set()
        for segment in self.segments:
            if segment.id in seen:
                raise ValueError(f"duplicate segment id: {segment.id}")
            seen.add(segment.id)
        return self

    # -- queries ----------------------------------------------------------

    def __len__(self) -> int:
        return len(self.segments)

    def __iter__(self) -> Any:
        return iter(self.segments)

    @property
    def speaking_duration(self) -> float:
        """Total seconds covered by segments (overlaps counted once)."""
        total = 0.0
        cursor = 0.0
        for segment in sorted(self.segments, key=lambda s: s.start):
            start = max(segment.start, cursor)
            if segment.end > start:
                total += segment.end - start
                cursor = segment.end
        return total

    @property
    def text(self) -> str:
        """The whole transcript as plain text, one segment per line."""
        return "\n".join(segment.text for segment in self.segments if segment.text)

    def speaker_ids(self) -> list[str]:
        """Distinct speaker IDs, in order of first appearance."""
        seen: list[str] = []
        for segment in self.segments:
            if segment.speaker_id and segment.speaker_id not in seen:
                seen.append(segment.speaker_id)
        return seen

    def find(self, segment_id: str) -> Segment | None:
        return next((s for s in self.segments if s.id == segment_id), None)

    def index_of(self, segment_id: str) -> int:
        for index, segment in enumerate(self.segments):
            if segment.id == segment_id:
                return index
        raise ValidationError(
            f"No segment with ID {segment_id!r} exists in this transcript.",
            context={"segment_id": segment_id},
        )

    # -- transformations --------------------------------------------------
    #
    # All of these return a new Transcript. Nothing mutates in place, so undo
    # and cache invalidation stay simple.

    def sorted(self) -> Transcript:
        return self.model_copy(
            update={"segments": sorted(self.segments, key=lambda s: (s.start, s.end))}
        )

    def replace_segment(self, segment: Segment) -> Transcript:
        index = self.index_of(segment.id)
        segments = list(self.segments)
        segments[index] = segment
        return self.model_copy(update={"segments": segments})

    def split_segment(self, segment_id: str, timestamp: float) -> Transcript:
        index = self.index_of(segment_id)
        left, right = self.segments[index].split_at(timestamp)
        segments = list(self.segments)
        segments[index : index + 1] = [left, right]
        return self.model_copy(update={"segments": segments})

    def merge_segments(self, first_id: str, second_id: str) -> Transcript:
        """Merge two segments. They need not be adjacent, but must be distinct."""
        if first_id == second_id:
            raise ValidationError(
                "A segment cannot be merged with itself.",
                context={"segment_id": first_id},
            )
        first_index = self.index_of(first_id)
        second_index = self.index_of(second_id)
        low, high = sorted((first_index, second_index))

        merged = self.segments[low].merged_with(self.segments[high])
        segments = [s for i, s in enumerate(self.segments) if i not in (low, high)]
        segments.insert(low, merged)
        return self.model_copy(update={"segments": segments})

    def assign_speaker(self, segment_id: str, speaker_id: str | None) -> Transcript:
        segment = self.segments[self.index_of(segment_id)]
        return self.replace_segment(segment.model_copy(update={"speaker_id": speaker_id}))

    def rename_speaker(self, old_id: str, new_id: str) -> Transcript:
        """Repoint every reference from ``old_id`` to ``new_id``."""
        segments = [
            s.model_copy(update={"speaker_id": new_id}) if s.speaker_id == old_id else s
            for s in self.segments
        ]
        return self.model_copy(update={"segments": segments})

    def low_confidence_segments(self, threshold: float) -> list[Segment]:
        """Segments whose ASR confidence falls below ``threshold``.

        Hand-edited segments are excluded: a human has already reviewed them.
        """
        return [
            s
            for s in self.segments
            if s.confidence is not None and s.confidence < threshold and not s.is_edited
        ]


__all__ = [
    "Segment",
    "Transcript",
    "TranscriptSource",
    "Translation",
    "Word",
    "new_segment_id",
]
