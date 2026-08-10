"""The processing stages and how they depend on one another.

The dependency graph is the mechanism behind incremental processing: changing
the translation must invalidate TTS, mixing and muxing, but must *not* throw
away the transcription that took forty minutes to produce.

Declaring dependencies in one table -- rather than scattering
``if stage == ...`` checks through the pipeline -- is what makes
:func:`downstream_of` correct by construction.
"""

from __future__ import annotations

from enum import Enum


class Stage(str, Enum):
    """A unit of processing that can be checkpointed and resumed.

    The numeric prefixes in the values give checkpoint directories a natural
    sort order on disk, which makes a project directory readable by hand.
    """

    MEDIA_PROBE = "01_media_probe"
    AUDIO_EXTRACT = "02_audio_extract"
    ASR = "03_asr"
    DIARIZATION = "04_diarization"
    TRANSLATION = "05_translation"
    TTS = "06_tts"
    MIX = "07_mix"
    MUX = "08_mux"

    @property
    def label(self) -> str:
        return _STAGE_LABELS[self]

    @property
    def order(self) -> int:
        return STAGE_ORDER.index(self)


STAGE_ORDER: tuple[Stage, ...] = (
    Stage.MEDIA_PROBE,
    Stage.AUDIO_EXTRACT,
    Stage.ASR,
    Stage.DIARIZATION,
    Stage.TRANSLATION,
    Stage.TTS,
    Stage.MIX,
    Stage.MUX,
)

_STAGE_LABELS: dict[Stage, str] = {
    Stage.MEDIA_PROBE: "Inspecting media",
    Stage.AUDIO_EXTRACT: "Extracting audio",
    Stage.ASR: "Transcribing",
    Stage.DIARIZATION: "Identifying speakers",
    Stage.TRANSLATION: "Translating",
    Stage.TTS: "Generating voices",
    Stage.MIX: "Mixing audio",
    Stage.MUX: "Building output file",
}

#: Direct inputs of each stage. Transitive dependencies are derived, not listed.
STAGE_DEPENDENCIES: dict[Stage, frozenset[Stage]] = {
    Stage.MEDIA_PROBE: frozenset(),
    Stage.AUDIO_EXTRACT: frozenset({Stage.MEDIA_PROBE}),
    Stage.ASR: frozenset({Stage.AUDIO_EXTRACT}),
    # Diarization needs the extracted audio and the ASR timings it labels.
    Stage.DIARIZATION: frozenset({Stage.AUDIO_EXTRACT, Stage.ASR}),
    # Translation depends on the transcript. It does *not* depend on
    # diarization: re-running speaker detection must not discard translations.
    Stage.TRANSLATION: frozenset({Stage.ASR}),
    # TTS needs the translated text and the voice assigned to each speaker.
    Stage.TTS: frozenset({Stage.TRANSLATION, Stage.DIARIZATION}),
    Stage.MIX: frozenset({Stage.TTS, Stage.AUDIO_EXTRACT}),
    Stage.MUX: frozenset({Stage.MIX, Stage.MEDIA_PROBE}),
}


class StageState(str, Enum):
    """Where a stage has got to."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"

    @property
    def is_terminal(self) -> bool:
        return self in (
            StageState.COMPLETED,
            StageState.FAILED,
            StageState.CANCELLED,
            StageState.SKIPPED,
        )


def dependencies_of(stage: Stage) -> frozenset[Stage]:
    """Every stage ``stage`` transitively depends on."""
    resolved: set[Stage] = set()
    pending = list(STAGE_DEPENDENCIES[stage])
    while pending:
        current = pending.pop()
        if current in resolved:
            continue
        resolved.add(current)
        pending.extend(STAGE_DEPENDENCIES[current])
    return frozenset(resolved)


def downstream_of(stage: Stage) -> frozenset[Stage]:
    """Every stage that must be invalidated when ``stage`` changes.

    This is the transitive closure of the reverse dependency edges. It is what
    the cache consults to decide how much work a change actually costs.
    """
    dependents = {s for s in Stage if stage in dependencies_of(s)}
    return frozenset(dependents)


def stages_up_to(stage: Stage) -> tuple[Stage, ...]:
    """The pipeline prefix ending at ``stage``, in execution order."""
    return STAGE_ORDER[: STAGE_ORDER.index(stage) + 1]


__all__ = [
    "STAGE_DEPENDENCIES",
    "STAGE_ORDER",
    "Stage",
    "StageState",
    "dependencies_of",
    "downstream_of",
    "stages_up_to",
]
