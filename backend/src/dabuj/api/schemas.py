"""Request and response schemas for the HTTP API.

Separate from the domain models on purpose. The domain is free to change shape;
the wire format is a contract with the frontend and changes only deliberately.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dabuj.domain.quality import Device, Precision, QualityProfile


class ErrorResponse(BaseModel):
    """The single error shape every failing endpoint returns."""

    code: str
    summary: str
    reason: str | None = None
    suggestions: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Absolute path to a local media file. Validated by the server.
    source_path: str
    name: str | None = None
    source_language: str = "auto"
    target_language: str | None = None
    quality: QualityProfile | None = None
    asr_model: str | None = None
    #: Copy the media into the project rather than referencing it in place.
    import_media: bool = True


class TranscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    language: str | None = None
    model_id: str | None = None
    device: Device | None = None
    precision: Precision | None = None
    word_timestamps: bool = True
    vad_filter: bool | None = None
    beam_size: int | None = Field(default=None, ge=1, le=10)
    force: bool = False


class InstallModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str
    force: bool = False


class SegmentUpdateRequest(BaseModel):
    """An edit to one segment. Every field is optional; only what is sent changes."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    speaker_id: str | None = None
    start: float | None = Field(default=None, ge=0.0)
    end: float | None = Field(default=None, ge=0.0)


class SplitSegmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: float = Field(ge=0.0)


class MergeSegmentsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_id: str
    second_id: str


class RenameSpeakerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: str = "srt"
    language: str | None = None
    include_speakers: bool = False


__all__ = [
    "CreateProjectRequest",
    "ErrorResponse",
    "ExportRequest",
    "InstallModelRequest",
    "MergeSegmentsRequest",
    "RenameSpeakerRequest",
    "SegmentUpdateRequest",
    "SplitSegmentRequest",
    "TranscribeRequest",
]
