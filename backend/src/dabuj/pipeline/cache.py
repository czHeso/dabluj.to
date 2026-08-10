"""Cache keys and dependency-aware invalidation.

Processing a film can take hours, so recomputing a stage that did not need to
change is the most expensive mistake this application can make. The rule:

    A stage's output is reusable exactly when its *inputs* are unchanged.

"Inputs" means the source content, the model and its revision, the provider
version, and the settings that affect the result -- never a wall-clock time or
a path. So a project moved to another folder still hits its cache, while
changing the beam size correctly misses it.

Large files are fingerprinted by ``(size, mtime_ns)`` rather than by hashing
their contents. Hashing a 12 GB source file on every run would cost more than
the stage being cached. :func:`content_hash` exists for when a real digest is
genuinely needed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dabuj.pipeline.stages import Stage, downstream_of

_HASH_CHUNK = 1024 * 1024
#: Key length. 128 bits of a SHA-256 is ample for a local cache namespace and
#: keeps directory names readable.
_KEY_LENGTH = 32


def file_fingerprint(path: Path) -> str:
    """Cheap identity for a file: size and modification time.

    Deliberately not a content hash -- see the module docstring. The risk is a
    file modified within the filesystem's timestamp granularity without its
    size changing, which does not happen for media files in practice.
    """
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def content_hash(path: Path) -> str:
    """True SHA-256 of a file's contents, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> Any:
    """Reduce a value to something that serialises identically every time.

    Dict ordering, ``Path`` separators and enum representations all vary in
    ways that would produce spurious cache misses, so each is normalised.
    """
    if isinstance(value, Path):
        return f"path:{value.name}"
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "value"):  # Enum
        return _canonical(value.value)
    return str(value)


@dataclass(frozen=True, slots=True)
class CacheKey:
    """An identifier for one stage's output under one set of inputs."""

    stage: Stage
    digest: str

    @property
    def value(self) -> str:
        return f"{self.stage.value}-{self.digest}"

    def __str__(self) -> str:
        return self.value


def compute_cache_key(
    stage: Stage,
    *,
    inputs: dict[str, Any] | None = None,
    model_id: str | None = None,
    model_revision: str | None = None,
    provider: str | None = None,
    provider_version: str | None = None,
    settings: dict[str, Any] | None = None,
) -> CacheKey:
    """Derive a stage's cache key from everything that affects its output.

    Args:
        stage: The stage being keyed.
        inputs: Fingerprints of upstream artefacts, e.g.
            ``{"audio": file_fingerprint(wav)}``.
        model_id: Model used, if any.
        model_revision: Pinned revision, so upgrading a model invalidates.
        provider: Provider name.
        provider_version: Provider version, so a runtime upgrade that changes
            output invalidates too.
        settings: Options that affect the result.

    Returns:
        A deterministic :class:`CacheKey`.
    """
    payload = {
        "stage": stage.value,
        "inputs": _canonical(inputs or {}),
        "model": {"id": model_id, "revision": model_revision},
        "provider": {"name": provider, "version": provider_version},
        "settings": _canonical(settings or {}),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:_KEY_LENGTH]
    return CacheKey(stage=stage, digest=digest)


def stages_to_invalidate(changed: Stage) -> frozenset[Stage]:
    """Which stages must be redone when ``changed`` produces a new result.

    The changed stage itself plus everything transitively downstream. This is
    what makes "the user edited a translation" cost a re-dub rather than a
    re-transcription.
    """
    return frozenset({changed}) | downstream_of(changed)


__all__ = [
    "CacheKey",
    "compute_cache_key",
    "content_hash",
    "file_fingerprint",
    "stages_to_invalidate",
]
