"""The application context: one object wiring the collaborators together.

Built once at start-up by whichever entry point is running (CLI or server) and
passed down. Constructing it is cheap -- nothing here loads a model or touches
the network -- so tests can build a fully isolated context pointing at a
temporary directory in a single call.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from dabuj.config.paths import StoragePaths
from dabuj.config.settings import AppSettings, load_settings
from dabuj.hardware.detect import SystemInfo, detect_system
from dabuj.hardware.profiles import ProfileRecommendation, recommend_profile
from dabuj.media.ffmpeg import FFmpegService
from dabuj.models.registry import ModelRegistry
from dabuj.projects.store import ProjectStore


@dataclass
class AppContext:
    """Everything the services need, resolved once."""

    settings: AppSettings
    paths: StoragePaths

    @staticmethod
    def create(
        *,
        data_dir: Path | None = None,
        settings: AppSettings | None = None,
    ) -> AppContext:
        """Build a context, creating the storage directories if needed.

        Args:
            data_dir: Override the root data directory. Mainly for tests and
                portable installs.
            settings: Pre-built settings. Loaded from disk when omitted.
        """
        base = StoragePaths.rooted_at(data_dir) if data_dir else StoragePaths.default()
        resolved = settings if settings is not None else load_settings(base.config_file)
        paths = resolved.storage_paths(base).ensure()
        return AppContext(settings=resolved, paths=paths)

    # Cached because each is either mildly expensive (binary lookup, hardware
    # probing) or should be a single shared instance (registry, store).

    @cached_property
    def ffmpeg(self) -> FFmpegService:
        return FFmpegService(
            ffmpeg_path=self.settings.ffmpeg_path,
            ffprobe_path=self.settings.ffprobe_path,
        )

    @cached_property
    def models(self) -> ModelRegistry:
        return ModelRegistry(self.paths.models_dir)

    @cached_property
    def projects(self) -> ProjectStore:
        return ProjectStore(self.paths.projects_dir)

    @cached_property
    def system(self) -> SystemInfo:
        return detect_system(self.paths.data_dir)

    @cached_property
    def recommendation(self) -> ProfileRecommendation:
        return recommend_profile(self.system)


__all__ = ["AppContext"]
