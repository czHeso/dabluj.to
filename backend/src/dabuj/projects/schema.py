"""The persisted project document.

A project is a directory. ``project.json`` is its manifest, and it is designed
to be *reopenable*: everything needed to resume processing, reproduce a result
or diagnose a bad one is recorded in it.

Two decisions shape this schema:

**Schema versioning from day one.** ``schema_version`` is written on every save
and checked on every load. A project written by a newer Dabuj is refused with
an explanation rather than being silently misread.

**Paths are stored relative to the project directory.** A project folder can be
moved, renamed, copied to another machine or synced, and it still opens. An
absolute path baked into the manifest would break all of that.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dabuj.domain.media import MediaInfo
from dabuj.domain.quality import Device, Precision, QualityProfile
from dabuj.domain.speaker import Speaker
from dabuj.domain.transcript import Transcript
from dabuj.pipeline.stages import Stage, StageState
from dabuj.version import PROJECT_SCHEMA_VERSION, __version__


def new_project_id() -> str:
    return uuid.uuid4().hex[:12]


class SourceMedia(BaseModel):
    """The input file, described relative to the project directory."""

    model_config = ConfigDict(validate_assignment=True)

    #: Path relative to the project root, POSIX-style so it is portable.
    relative_path: str
    original_filename: str
    #: ``None`` when the media was referenced in place rather than imported.
    original_path: str | None = None
    size_bytes: int = Field(default=0, ge=0)
    #: Cached probe result, so reopening a project does not require the file.
    info: MediaInfo | None = None

    def resolve(self, project_dir: Path) -> Path:
        return project_dir / PurePosixPath(self.relative_path)


class ProjectSettings(BaseModel):
    """Processing settings frozen into the project when it was created.

    Frozen deliberately: changing the application default months later must not
    silently change what an existing project would produce on resume.
    """

    model_config = ConfigDict(validate_assignment=True)

    source_language: str = "auto"
    target_language: str | None = None
    quality: QualityProfile = QualityProfile.BALANCED
    device: Device = Device.AUTO
    precision: Precision = Precision.AUTO

    asr_model: str | None = None
    diarization_model: str | None = None
    translation_model: str | None = None
    tts_model: str | None = None

    word_timestamps: bool = True
    vad_filter: bool = True
    beam_size: int = Field(default=5, ge=1, le=10)
    #: Expected speaker count for diarization; ``None`` means auto-detect.
    expected_speakers: int | None = Field(default=None, ge=1)


class StageRecord(BaseModel):
    """The outcome of one pipeline stage.

    ``cache_key`` is what makes resume correct: on reopening, a stage is only
    treated as done if its recomputed key still matches the stored one. Change
    the model or the settings and the key changes, so the stale result is
    correctly discarded.
    """

    model_config = ConfigDict(validate_assignment=True)

    state: StageState = StageState.PENDING
    cache_key: str | None = None
    started_at: float | None = None
    completed_at: float | None = None
    duration_seconds: float | None = None
    #: Artefacts produced, as project-relative POSIX paths.
    artifacts: dict[str, str] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.state is StageState.COMPLETED


class ProcessingRecord(BaseModel):
    """Provenance, so a result can be reproduced or explained later.

    Records software and model identities only. Nothing that identifies the
    *machine* -- no serial numbers, hostnames or user names (docs/PRIVACY.md).
    """

    model_config = ConfigDict(validate_assignment=True)

    app_version: str = __version__
    os_name: str | None = None
    ffmpeg_version: str | None = None
    processed_at: float | None = None

    #: Provider identity per task, e.g. ``{"asr": {...}}``.
    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    quality_profile: QualityProfile | None = None
    device: Device | None = None
    precision: Precision | None = None
    realtime_factor: float | None = None


class ProjectDocument(BaseModel):
    """The complete contents of ``project.json``."""

    model_config = ConfigDict(validate_assignment=True)

    schema_version: int = PROJECT_SCHEMA_VERSION
    app_version: str = __version__

    id: str = Field(default_factory=new_project_id)
    name: str
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    source: SourceMedia
    settings: ProjectSettings = Field(default_factory=ProjectSettings)

    transcript: Transcript = Field(default_factory=Transcript)
    speakers: dict[str, Speaker] = Field(default_factory=dict)

    stages: dict[Stage, StageRecord] = Field(default_factory=dict)
    processing: ProcessingRecord = Field(default_factory=ProcessingRecord)

    #: User-visible warnings accumulated during processing.
    warnings: list[str] = Field(default_factory=list)
    #: Exports written so far, as ``{format: relative_path}``.
    exports: dict[str, str] = Field(default_factory=dict)

    # -- stage helpers ----------------------------------------------------

    def stage(self, stage: Stage) -> StageRecord:
        """The record for a stage, creating a pending one if absent."""
        return self.stages.get(stage) or StageRecord()

    def is_stage_valid(self, stage: Stage, cache_key: str) -> bool:
        """Whether a completed stage's stored result still matches its inputs."""
        record = self.stages.get(stage)
        return (
            record is not None
            and record.state is StageState.COMPLETED
            and record.cache_key == cache_key
        )

    def set_stage(self, stage: Stage, record: StageRecord) -> None:
        self.stages[stage] = record
        self.updated_at = time.time()

    def invalidate(self, stages: frozenset[Stage] | set[Stage]) -> None:
        """Mark stages as pending, discarding their cached results.

        Used when the user changes something upstream. The stage's artefacts
        are left on disk; the cache key mismatch already prevents them from
        being reused, and deleting them would break undo.
        """
        for stage in stages:
            if stage in self.stages:
                self.stages[stage] = StageRecord(state=StageState.PENDING)
        self.updated_at = time.time()

    @property
    def completed_stages(self) -> tuple[Stage, ...]:
        return tuple(
            stage
            for stage, record in sorted(self.stages.items(), key=lambda kv: kv[0].order)
            if record.state is StageState.COMPLETED
        )

    def add_warning(self, message: str) -> None:
        """Record a user-visible warning, avoiding duplicates."""
        if message not in self.warnings:
            self.warnings.append(message)


__all__ = [
    "ProcessingRecord",
    "ProjectDocument",
    "ProjectSettings",
    "SourceMedia",
    "StageRecord",
    "new_project_id",
]
