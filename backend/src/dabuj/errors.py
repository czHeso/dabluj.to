"""Explicit, user-facing error types.

A normal user must never be shown only a traceback (see docs/ARCHITECTURE.md).
Every error raised by Dabuj therefore carries three separate things:

``summary``
    One short sentence saying what failed, in plain language.
``reason``
    Optional detail explaining *why* it failed.
``suggestions``
    Concrete next steps the user can actually take.

The technical traceback still goes to the log file; it is never the only thing
the user sees. ``DabujError.to_payload()`` produces the structure that both the
CLI renderer and the HTTP error handler consume, so the two surfaces cannot
drift apart.
"""

from __future__ import annotations

from typing import Any


class DabujError(Exception):
    """Base class for every error Dabuj raises deliberately.

    Anything that escapes as a bare ``Exception`` is a bug: it will be reported
    to the user as an internal error and logged with a full traceback.
    """

    #: Stable machine-readable code, used by the API and the frontend.
    code = "internal_error"
    #: HTTP status the API layer should map this to.
    http_status = 500

    def __init__(
        self,
        summary: str,
        *,
        reason: str | None = None,
        suggestions: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(summary)
        self.summary = summary
        self.reason = reason
        self.suggestions = suggestions or []
        self.context = context or {}

    def to_payload(self) -> dict[str, Any]:
        """Render the error as a serialisable dict for the CLI and the API."""
        return {
            "code": self.code,
            "summary": self.summary,
            "reason": self.reason,
            "suggestions": list(self.suggestions),
            "context": dict(self.context),
        }

    def __str__(self) -> str:
        parts = [self.summary]
        if self.reason:
            parts.append(f"Reason: {self.reason}")
        if self.suggestions:
            parts.append("Try: " + "; ".join(self.suggestions))
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Input and configuration
# ---------------------------------------------------------------------------


class ConfigurationError(DabujError):
    """The application is misconfigured, e.g. an unwritable storage directory."""

    code = "configuration_error"
    http_status = 500


class ValidationError(DabujError):
    """The user supplied something Dabuj cannot accept."""

    code = "validation_error"
    http_status = 400


class NotFoundError(DabujError):
    """A referenced project, job, model or segment does not exist."""

    code = "not_found"
    http_status = 404


class UnsafePathError(ValidationError):
    """A path escaped the directory it was required to stay inside.

    Raised by the path-confinement helpers that guard every filesystem-facing
    API. See docs/SECURITY.md for the threat model.
    """

    code = "unsafe_path"
    http_status = 400


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------


class MediaError(DabujError):
    """Something went wrong handling a media file."""

    code = "media_error"
    http_status = 422


class FFmpegNotFoundError(MediaError):
    """FFmpeg is not installed or not on PATH."""

    code = "ffmpeg_not_found"
    http_status = 503

    def __init__(self, searched: list[str] | None = None) -> None:
        super().__init__(
            "FFmpeg could not be found.",
            reason=(
                "Dabuj uses FFmpeg for all media probing, audio extraction and "
                "muxing. It is not bundled with the application."
            ),
            suggestions=[
                "Windows: winget install Gyan.FFmpeg",
                "macOS: brew install ffmpeg",
                "Debian/Ubuntu: sudo apt install ffmpeg",
                "Or set the ffmpeg_path option in your Dabuj config file",
            ],
            context={"searched": searched or []},
        )


class FFmpegExecutionError(MediaError):
    """An FFmpeg invocation exited with a non-zero status."""

    code = "ffmpeg_failed"
    http_status = 422


class UnsupportedMediaError(MediaError):
    """The file exists but contains nothing Dabuj can work with."""

    code = "unsupported_media"
    http_status = 422


# ---------------------------------------------------------------------------
# Models and providers
# ---------------------------------------------------------------------------


class ModelError(DabujError):
    """Base class for model registry and model loading failures."""

    code = "model_error"
    http_status = 500


class ModelNotInstalledError(ModelError):
    """A model was requested that has not been downloaded yet."""

    code = "model_not_installed"
    http_status = 409

    def __init__(self, model_id: str) -> None:
        super().__init__(
            f"The model {model_id!r} is not installed.",
            reason="Dabuj never downloads large models without explicit consent.",
            suggestions=[
                f"Install it with: dabuj models install {model_id}",
                "List what is available with: dabuj models list",
            ],
            context={"model_id": model_id},
        )


class ModelDownloadError(ModelError):
    """A model download failed, was corrupt, or failed verification."""

    code = "model_download_failed"
    http_status = 502


class ProviderUnavailableError(ModelError):
    """A provider's runtime is not importable or not usable on this machine."""

    code = "provider_unavailable"
    http_status = 503


class InsufficientResourcesError(ModelError):
    """Not enough RAM, VRAM or disk to do what was asked.

    Carries the numbers so the UI can render the "needs 8 GB, you have 5.7 GB"
    message described in docs/ARCHITECTURE.md rather than a generic failure.
    """

    code = "insufficient_resources"
    http_status = 507


# ---------------------------------------------------------------------------
# Jobs and pipeline
# ---------------------------------------------------------------------------


class PipelineError(DabujError):
    """A processing stage failed."""

    code = "pipeline_error"
    http_status = 500


class CancelledError(DabujError):
    """The user cancelled the operation.

    Deliberately *not* a subclass of :class:`PipelineError`: cancellation is a
    normal outcome, not a failure, and the two are reported differently.
    """

    code = "cancelled"
    http_status = 499

    def __init__(self, what: str = "The operation") -> None:
        super().__init__(f"{what} was cancelled.")


class ProjectError(DabujError):
    """A project could not be created, opened, migrated or saved."""

    code = "project_error"
    http_status = 422


class ProjectSchemaError(ProjectError):
    """A project file has a schema version this build cannot read."""

    code = "project_schema_unsupported"
    http_status = 422


__all__ = [
    "CancelledError",
    "ConfigurationError",
    "DabujError",
    "FFmpegExecutionError",
    "FFmpegNotFoundError",
    "InsufficientResourcesError",
    "MediaError",
    "ModelDownloadError",
    "ModelError",
    "ModelNotInstalledError",
    "NotFoundError",
    "PipelineError",
    "ProjectError",
    "ProjectSchemaError",
    "ProviderUnavailableError",
    "UnsafePathError",
    "UnsupportedMediaError",
    "ValidationError",
]
