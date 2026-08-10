"""User-editable application settings, persisted as TOML.

Four layers of configuration exist in Dabuj, and they are kept apart on
purpose:

1. **Application defaults** -- the field defaults in this module.
2. **User settings** -- ``dabuj.toml`` in the data directory. This module.
3. **Project settings** -- frozen into ``project.json`` when a project is
   created, so reopening a project reproduces its original processing.
4. **Per-run overrides** -- CLI flags and API request bodies.

Later layers win. Nothing here holds secrets; if optional cloud providers are
ever added, their credentials come from the environment instead (see
docs/PRIVACY.md).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel, ConfigDict, Field

from dabuj.config.paths import StoragePaths
from dabuj.domain.quality import Device, Precision, QualityProfile
from dabuj.errors import ConfigurationError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on Python 3.10
    import tomli as tomllib


class ServerSettings(BaseModel):
    """How the local web server binds.

    The defaults are the secure ones. ``allow_lan`` exists because some users
    genuinely want to reach Dabuj from another machine, but turning it on is a
    deliberate act and the application warns loudly when it is set.
    """

    model_config = ConfigDict(validate_assignment=True)

    host: str = "127.0.0.1"
    port: int = Field(default=7860, ge=1, le=65535)
    #: Try the next port when the preferred one is taken, rather than failing.
    auto_port: bool = True
    open_browser: bool = True
    #: Bind to 0.0.0.0 instead of loopback. Off by default; see docs/SECURITY.md.
    allow_lan: bool = False

    @property
    def effective_host(self) -> str:
        return "0.0.0.0" if self.allow_lan else self.host  # noqa: S104 - explicit opt-in


class ProcessingSettings(BaseModel):
    """Defaults for the processing pipeline.

    ``asr_model`` being ``None`` means "let the quality profile decide", which
    is what most users want. Naming a model here pins it regardless of profile.
    """

    model_config = ConfigDict(validate_assignment=True)

    quality: QualityProfile | None = None
    device: Device = Device.AUTO
    precision: Precision = Precision.AUTO
    asr_model: str | None = None
    #: Number of CPU threads for inference. 0 means "let the runtime decide".
    cpu_threads: int = Field(default=0, ge=0)
    #: Below this ASR confidence, a segment is flagged in the quality report.
    low_confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    #: Run voice activity detection before recognition. Cuts silence, which
    #: both speeds things up and reduces Whisper's tendency to hallucinate.
    vad_filter: bool = True


class PrivacySettings(BaseModel):
    """Privacy switches. Every default is the private one."""

    model_config = ConfigDict(validate_assignment=True)

    #: Dabuj sends no telemetry. The field exists so the UI can show it is off
    #: and so any future opt-in has an obvious home.
    telemetry: bool = False
    #: Master switch for any provider that would transmit media off the machine.
    #: While false, cloud providers cannot be selected at all.
    allow_cloud_providers: bool = False


class StorageSettings(BaseModel):
    """Optional relocations for the large directories."""

    model_config = ConfigDict(validate_assignment=True)

    models_dir: Path | None = None
    projects_dir: Path | None = None
    cache_dir: Path | None = None


class AppSettings(BaseModel):
    """The complete user configuration."""

    model_config = ConfigDict(validate_assignment=True)

    storage: StorageSettings = Field(default_factory=StorageSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    processing: ProcessingSettings = Field(default_factory=ProcessingSettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)

    #: Explicit path to the ffmpeg binary, for installs that are not on PATH.
    ffmpeg_path: Path | None = None
    ffprobe_path: Path | None = None

    debug_logging: bool = False

    def storage_paths(self, base: StoragePaths | None = None) -> StoragePaths:
        """Apply the storage overrides to a base layout."""
        return (base or StoragePaths.default()).with_overrides(
            models_dir=self.storage.models_dir,
            projects_dir=self.storage.projects_dir,
            cache_dir=self.storage.cache_dir,
        )


def _prune(value: Any) -> Any:
    """Drop ``None`` values so the written TOML shows only real settings.

    TOML has no null, and an empty table is friendlier to hand-edit than one
    full of commented-out placeholders.
    """
    if isinstance(value, dict):
        return {k: _prune(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_prune(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def load_settings(config_file: Path) -> AppSettings:
    """Read settings from ``config_file``, or return defaults if it is absent.

    A missing file is normal on first run and is not an error. A malformed or
    unreadable file *is* an error: silently falling back to defaults would hide
    a typo that changes where the user's models live.

    Raises:
        ConfigurationError: If the file exists but cannot be parsed or validated.
    """
    if not config_file.exists():
        return AppSettings()

    try:
        with config_file.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(
            f"The configuration file {config_file.name} is not valid TOML.",
            reason=str(exc),
            suggestions=[
                f"Fix the syntax in {config_file}",
                "Or delete the file to start again from the defaults",
            ],
            context={"config_file": str(config_file)},
        ) from exc
    except OSError as exc:
        raise ConfigurationError(
            f"Dabuj could not read {config_file}.",
            reason=str(exc),
            context={"config_file": str(config_file)},
        ) from exc

    try:
        return AppSettings.model_validate(raw)
    except ValueError as exc:
        raise ConfigurationError(
            f"The configuration file {config_file.name} contains invalid values.",
            reason=str(exc),
            suggestions=[f"Correct the offending option in {config_file}"],
            context={"config_file": str(config_file)},
        ) from exc


def save_settings(settings: AppSettings, config_file: Path) -> None:
    """Write settings to ``config_file`` atomically.

    The write goes to a temporary file in the same directory and is then moved
    into place, so an interrupted save cannot leave a truncated config behind.

    Raises:
        ConfigurationError: If the file cannot be written.
    """
    payload = _prune(settings.model_dump(mode="json", exclude_none=True))
    temp_file = config_file.with_suffix(config_file.suffix + ".partial")
    try:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with temp_file.open("wb") as handle:
            tomli_w.dump(payload, handle)
        temp_file.replace(config_file)
    except OSError as exc:
        temp_file.unlink(missing_ok=True)
        raise ConfigurationError(
            f"Dabuj could not save settings to {config_file}.",
            reason=str(exc),
            suggestions=["Check that you have permission to write to that directory"],
            context={"config_file": str(config_file)},
        ) from exc


__all__ = [
    "AppSettings",
    "PrivacySettings",
    "ProcessingSettings",
    "ServerSettings",
    "StorageSettings",
    "load_settings",
    "save_settings",
]
