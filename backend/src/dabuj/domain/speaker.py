"""Speakers.

A speaker is created by diarization as an anonymous label (``SPEAKER_00``) and
may then be renamed by the user (``Anna``). The *ID* never changes, because
segments, voice mappings and generated audio all refer to it; only
``display_name`` does.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_LABEL_PATTERN = re.compile(r"^SPEAKER_(\d+)$")


def default_speaker_id(index: int) -> str:
    """The canonical anonymous label for the ``index``-th detected speaker."""
    return f"SPEAKER_{index:02d}"


class Speaker(BaseModel):
    """One participant in the recording.

    ``voice_id`` is the dubbing voice assigned to this speaker. It lives here
    rather than in a separate mapping table so that a project carries its
    casting decisions with it.
    """

    model_config = ConfigDict(validate_assignment=True)

    id: str
    display_name: str | None = None
    #: Target-language TTS voice assigned to this speaker, when chosen.
    voice_id: str | None = None
    #: Seconds of speech attributed to this speaker, for sorting the cast list.
    total_duration: float = Field(default=0.0, ge=0.0)
    segment_count: int = Field(default=0, ge=0)
    #: Acoustic properties measured from the source audio, used only to
    #: *suggest* a target voice. Never presented as facts about the person.
    acoustics: dict[str, Any] = Field(default_factory=dict)

    @property
    def name(self) -> str:
        """What to show in the UI: the user's name for them, else the label."""
        return self.display_name or self.id

    @property
    def is_named(self) -> bool:
        return bool(self.display_name)

    @property
    def index(self) -> int | None:
        """The ordinal from an auto-generated label, if this is one."""
        match = _LABEL_PATTERN.match(self.id)
        return int(match.group(1)) if match else None

    def renamed(self, display_name: str | None) -> Speaker:
        """Return a copy with a new display name; blank clears it."""
        cleaned = (display_name or "").strip()
        return self.model_copy(update={"display_name": cleaned or None})

    def with_voice(self, voice_id: str | None) -> Speaker:
        return self.model_copy(update={"voice_id": voice_id})


__all__ = ["Speaker", "default_speaker_id"]
