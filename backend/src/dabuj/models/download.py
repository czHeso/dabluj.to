"""Secure model downloading.

Rules, all of which exist because getting them wrong is how a model manager
becomes a remote code execution vector (docs/SECURITY.md):

* **The file list is resolved before anything is fetched**, so the user is told
  the real total size and can refuse.
* **Every path from the remote index is confined** to the destination
  directory. A repository claiming a file called ``../../ssh/id_rsa`` is
  rejected, not written.
* **Downloads land in ``.partial`` files** and are renamed only after
  verification, so an interrupted download can never masquerade as a complete
  model.
* **Checksums are verified** whenever the source publishes one. Hugging Face
  exposes the SHA-256 of every LFS file, which covers the model weights -- the
  files that actually matter.
* **Resumable**: an existing ``.partial`` is continued with a Range request
  rather than restarted, which matters at three gigabytes.
* **Cancellable** at chunk granularity.

No archives are extracted. Dabuj downloads plain files only, so there is no
zip-slip surface at all.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from dabuj.config.paths import resolve_within
from dabuj.errors import ModelDownloadError, UnsafePathError
from dabuj.logging import get_logger
from dabuj.models.catalog import ModelSpec
from dabuj.net import create_ssl_context
from dabuj.pipeline.cancellation import CancellationToken, NullCancellationToken

logger = get_logger(__name__)

HF_ENDPOINT = "https://huggingface.co"
_CHUNK_SIZE = 1024 * 1024
_INDEX_TIMEOUT = 30.0
_DOWNLOAD_TIMEOUT = httpx.Timeout(30.0, read=300.0)
_MAX_REDIRECTS = 5


@dataclass(frozen=True, slots=True)
class RemoteFile:
    """One file to fetch, as described by the remote index."""

    path: str
    size_bytes: int
    #: SHA-256 from the source, when published. ``None`` means unverifiable.
    sha256: str | None = None

    @property
    def url_path(self) -> str:
        return self.path


@dataclass(frozen=True, slots=True)
class DownloadPlan:
    """What installing a model will actually cost, resolved before any transfer."""

    spec: ModelSpec
    files: tuple[RemoteFile, ...]
    revision: str

    @property
    def total_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)

    @property
    def verifiable_bytes(self) -> int:
        """Bytes covered by a published checksum."""
        return sum(f.size_bytes for f in self.files if f.sha256)


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    """A progress tick during installation."""

    model_id: str
    #: File currently transferring.
    current_file: str
    #: Bytes transferred across the whole model.
    downloaded_bytes: int
    total_bytes: int
    files_completed: int
    total_files: int

    @property
    def fraction(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return min(1.0, self.downloaded_bytes / self.total_bytes)


ProgressHook = Callable[[DownloadProgress], None]


def _matches_patterns(path: str, patterns: tuple[str, ...]) -> bool:
    """Whether ``path`` matches any glob in ``patterns``. Empty means "all"."""
    if not patterns:
        return True
    from fnmatch import fnmatch  # noqa: PLC0415 - trivial, only needed here

    name = path.rsplit("/", 1)[-1]
    return any(fnmatch(name, pattern) or fnmatch(path, pattern) for pattern in patterns)


class ModelDownloader:
    """Downloads model files from Hugging Face into the local models directory."""

    def __init__(self, models_dir: Path, *, endpoint: str = HF_ENDPOINT) -> None:
        self._models_dir = models_dir
        self._endpoint = endpoint.rstrip("/")

    # -- planning ---------------------------------------------------------

    def plan(self, spec: ModelSpec) -> DownloadPlan:
        """Resolve the exact file list and size before downloading anything.

        Raises:
            ModelDownloadError: If the repository cannot be listed.
        """
        url = f"{self._endpoint}/api/models/{spec.repo_id}/tree/{spec.revision}"
        try:
            response = httpx.get(
                url,
                timeout=_INDEX_TIMEOUT,
                follow_redirects=True,
                params={"recursive": "true"},
                verify=create_ssl_context(),
            )
            response.raise_for_status()
            entries = response.json()
        except httpx.HTTPStatusError as exc:
            raise ModelDownloadError(
                f"Could not look up the model {spec.name!r}.",
                reason=(
                    f"The download source returned HTTP {exc.response.status_code}. "
                    + (
                        "This model may require accepting its license terms first."
                        if exc.response.status_code in (401, 403)
                        else "The repository may have been moved or renamed."
                    )
                ),
                suggestions=(
                    [f"Open {spec.model_card} and accept the terms, then retry"]
                    if spec.model_card and exc.response.status_code in (401, 403)
                    else ["Check your internet connection and try again"]
                ),
                context={"model_id": spec.id, "repo_id": spec.repo_id},
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            certificate_problem = "certificate" in str(exc).lower()
            raise ModelDownloadError(
                f"Could not reach the download source for {spec.name!r}.",
                reason=str(exc),
                suggestions=(
                    [
                        "This looks like a certificate problem. If you are on a "
                        "corporate network, your organisation's root certificate "
                        "must be installed in the operating system trust store.",
                        "Check that huggingface.co is reachable from your browser",
                    ]
                    if certificate_problem
                    else ["Check your internet connection and try again"]
                ),
                context={"model_id": spec.id},
            ) from exc

        if not isinstance(entries, list):
            raise ModelDownloadError(
                f"The download source returned an unexpected listing for {spec.name!r}.",
                context={"model_id": spec.id},
            )

        files: list[RemoteFile] = []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("type") != "file":
                continue
            path = str(entry.get("path") or "")
            if not path or not _matches_patterns(path, spec.allow_patterns):
                continue

            lfs = entry.get("lfs") if isinstance(entry.get("lfs"), dict) else None
            size = int(lfs.get("size", 0)) if lfs else int(entry.get("size") or 0)
            # For LFS files the `oid` is the SHA-256 of the content.
            sha = str(lfs.get("oid")) if lfs and lfs.get("oid") else None

            files.append(RemoteFile(path=path, size_bytes=size, sha256=sha))

        if not files:
            raise ModelDownloadError(
                f"No downloadable files were found for {spec.name!r}.",
                reason=(
                    "The repository listing contained nothing matching this model's file patterns."
                ),
                context={"model_id": spec.id, "repo_id": spec.repo_id},
            )

        return DownloadPlan(spec=spec, files=tuple(files), revision=spec.revision)

    # -- downloading ------------------------------------------------------

    def download(
        self,
        plan: DownloadPlan,
        *,
        on_progress: ProgressHook | None = None,
        cancellation: CancellationToken | None = None,
    ) -> Path:
        """Execute a :class:`DownloadPlan`.

        Returns:
            The directory the model was installed into.

        Raises:
            CancelledError: If cancelled. Partial files are left in place so a
                later attempt can resume; they are never mistaken for a
                complete install because the marker file is written last.
            ModelDownloadError: On transfer or verification failure.
        """
        token = cancellation or NullCancellationToken()
        spec = plan.spec
        destination = self._models_dir / spec.id
        destination.mkdir(parents=True, exist_ok=True)

        downloaded = 0
        for index, remote in enumerate(plan.files):
            token.raise_if_cancelled(f"Downloading {spec.name}")

            try:
                target = resolve_within(destination, remote.path)
            except UnsafePathError as exc:
                raise ModelDownloadError(
                    f"The download for {spec.name!r} was rejected as unsafe.",
                    reason=(
                        f"The source listed a file path ({remote.path!r}) that would "
                        "write outside the model directory."
                    ),
                    context={"model_id": spec.id, "path": remote.path},
                ) from exc

            if target.exists() and target.stat().st_size == remote.size_bytes:
                # Already present and the right size; verify rather than refetch.
                if remote.sha256 is None or _sha256(target) == remote.sha256:
                    downloaded += remote.size_bytes
                    if on_progress:
                        on_progress(
                            DownloadProgress(
                                model_id=spec.id,
                                current_file=remote.path,
                                downloaded_bytes=downloaded,
                                total_bytes=plan.total_bytes,
                                files_completed=index + 1,
                                total_files=len(plan.files),
                            )
                        )
                    continue
                target.unlink()

            self._download_file(
                spec=spec,
                remote=remote,
                target=target,
                revision=plan.revision,
                already_done=downloaded,
                plan=plan,
                file_index=index,
                on_progress=on_progress,
                token=token,
            )
            downloaded += remote.size_bytes

        return destination

    def _download_file(
        self,
        *,
        spec: ModelSpec,
        remote: RemoteFile,
        target: Path,
        revision: str,
        already_done: int,
        plan: DownloadPlan,
        file_index: int,
        on_progress: ProgressHook | None,
        token: CancellationToken,
    ) -> None:
        """Transfer one file, resuming a ``.partial`` where possible."""
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        url = f"{self._endpoint}/{spec.repo_id}/resolve/{revision}/{remote.url_path}"

        resume_from = partial.stat().st_size if partial.exists() else 0
        if resume_from > remote.size_bytes:
            # A stale partial from a different revision: start again.
            partial.unlink()
            resume_from = 0

        headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
        mode = "ab" if resume_from else "wb"

        try:
            with httpx.stream(
                "GET",
                url,
                timeout=_DOWNLOAD_TIMEOUT,
                follow_redirects=True,
                headers=headers,
                verify=create_ssl_context(),
            ) as response:
                if resume_from and response.status_code == 200:
                    # The server ignored the Range header; restart cleanly.
                    mode, resume_from = "wb", 0
                response.raise_for_status()

                transferred = resume_from
                with partial.open(mode) as handle:
                    for chunk in response.iter_bytes(_CHUNK_SIZE):
                        if token.is_cancelled:
                            handle.flush()
                            token.raise_if_cancelled(f"Downloading {spec.name}")
                        handle.write(chunk)
                        transferred += len(chunk)
                        if on_progress:
                            on_progress(
                                DownloadProgress(
                                    model_id=spec.id,
                                    current_file=remote.path,
                                    downloaded_bytes=already_done + transferred - resume_from
                                    if resume_from
                                    else already_done + transferred,
                                    total_bytes=plan.total_bytes,
                                    files_completed=file_index,
                                    total_files=len(plan.files),
                                )
                            )
        except httpx.HTTPError as exc:
            raise ModelDownloadError(
                f"Downloading {remote.path} for {spec.name!r} failed.",
                reason=str(exc),
                suggestions=[
                    "Check your internet connection",
                    "Run the install again -- Dabuj resumes where it left off",
                ],
                context={"model_id": spec.id, "file": remote.path},
            ) from exc

        # Verify before the rename. A file that fails here never becomes visible
        # as part of an installed model.
        if remote.sha256:
            actual = _sha256(partial)
            if actual != remote.sha256:
                partial.unlink(missing_ok=True)
                raise ModelDownloadError(
                    f"The downloaded file {remote.path} did not match its checksum.",
                    reason=(
                        "This usually means the transfer was corrupted. It can also "
                        "indicate the file was tampered with in transit."
                    ),
                    suggestions=["Run the install again"],
                    context={
                        "model_id": spec.id,
                        "file": remote.path,
                        "expected": remote.sha256,
                        "actual": actual,
                    },
                )
        elif remote.size_bytes and partial.stat().st_size != remote.size_bytes:
            partial.unlink(missing_ok=True)
            raise ModelDownloadError(
                f"The downloaded file {remote.path} was the wrong size.",
                reason=(
                    f"Expected {remote.size_bytes} bytes but received {partial.stat().st_size}."
                ),
                suggestions=["Run the install again"],
                context={"model_id": spec.id, "file": remote.path},
            )

        partial.replace(target)


def _sha256(path: Path) -> str:
    """SHA-256 of a file, read in chunks so a 3 GB model does not fill RAM."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "HF_ENDPOINT",
    "DownloadPlan",
    "DownloadProgress",
    "ModelDownloader",
    "RemoteFile",
]
