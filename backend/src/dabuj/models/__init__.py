"""Model catalog, registry and downloader.

Dabuj never bundles model weights. This package is how they get onto the
machine: explicitly, verifiably, and only when the user asks.
"""

from dabuj.models.catalog import (
    BUILTIN_CATALOG,
    ModelSpec,
    ModelTask,
    find_model,
    models_for_task,
)
from dabuj.models.download import DownloadProgress, ModelDownloader
from dabuj.models.registry import InstalledModel, ModelRegistry

__all__ = [
    "BUILTIN_CATALOG",
    "DownloadProgress",
    "InstalledModel",
    "ModelDownloader",
    "ModelRegistry",
    "ModelSpec",
    "ModelTask",
    "find_model",
    "models_for_task",
]
