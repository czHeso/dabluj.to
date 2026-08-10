"""A local job queue for long-running work.

Deliberately small. This is a single-user desktop application, so there is no
Redis, no Celery and no broker -- just a queue, a worker thread and a
dictionary. Anything more would be infrastructure to maintain for no benefit.

What it does need to get right:

* **Work happens off the request thread.** A transcription can run for hours;
  it cannot live inside an HTTP request or a ``BackgroundTasks`` callback.
* **Serial execution by default.** Two jobs sharing one GPU will exhaust its
  memory, so the default concurrency is 1. It is a parameter, not a constant,
  because CPU-only stages could safely overlap later.
* **Cancellable.** Every job owns a token; cancelling a queued job removes it
  without ever starting it.
* **Observable.** Each job carries a reporter that both the WebSocket feed and
  the CLI subscribe to.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from dabuj.errors import CancelledError, DabujError, NotFoundError
from dabuj.logging import get_logger
from dabuj.pipeline.cancellation import CancellationToken
from dabuj.pipeline.progress import JobStatus, ProgressReporter
from dabuj.pipeline.stages import Stage

logger = get_logger(__name__)


class JobKind(str, Enum):
    """What a job does. Used for display and for future scheduling policy."""

    TRANSCRIBE = "transcribe"
    MODEL_INSTALL = "model_install"


#: A job's work function. Receives its reporter and token; returns any result.
JobFunction = Callable[[ProgressReporter, CancellationToken], Any]


@dataclass
class Job:
    """One queued or running unit of work."""

    id: str
    kind: JobKind
    title: str
    reporter: ProgressReporter
    cancellation: CancellationToken
    function: JobFunction

    project_id: str | None = None
    status: JobStatus = JobStatus.QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: Any = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialisable summary. Never includes the result payload, which may
        contain transcript text."""
        progress = self.reporter.stage_progress
        return {
            "id": self.id,
            "kind": self.kind.value,
            "title": self.title,
            "status": self.status.value,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "overall_progress": self.reporter.overall_fraction(),
            "error": self.error,
            "stages": [
                {
                    "stage": stage.value,
                    "label": stage.label,
                    "state": record.state.value,
                    "fraction": record.fraction,
                    "message": record.message,
                }
                for stage, record in sorted(progress.items(), key=lambda kv: kv[0].order)
            ],
        }


class JobManager:
    """Runs jobs on background worker threads.

    Thread-safe. Start it with :meth:`start` and stop it with :meth:`shutdown`;
    the API server does both through its lifespan handler.
    """

    def __init__(self, *, concurrency: int = 1, history_limit: int = 100) -> None:
        """
        Args:
            concurrency: Jobs to run at once. Defaults to 1 because GPU-heavy
                work cannot safely overlap.
            history_limit: Finished jobs to retain for the UI before pruning.
        """
        self._queue: queue.Queue[str] = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._workers: list[threading.Thread] = []
        self._concurrency = max(1, concurrency)
        self._history_limit = history_limit
        self._shutdown = threading.Event()

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Start the worker threads. Idempotent."""
        if self._workers:
            return
        self._shutdown.clear()
        for index in range(self._concurrency):
            worker = threading.Thread(target=self._work, name=f"dabuj-worker-{index}", daemon=True)
            worker.start()
            self._workers.append(worker)
        logger.info("job manager started", extra={"workers": self._concurrency})

    def shutdown(self, *, wait: float = 5.0) -> None:
        """Cancel everything in flight and stop the workers."""
        self._shutdown.set()
        with self._lock:
            for job in self._jobs.values():
                if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                    job.cancellation.cancel()

        # Unblock any worker parked on an empty queue.
        for _ in self._workers:
            self._queue.put("")

        for worker in self._workers:
            worker.join(timeout=wait)
        self._workers.clear()

    # -- submission -------------------------------------------------------

    def submit(
        self,
        kind: JobKind,
        title: str,
        function: JobFunction,
        *,
        project_id: str | None = None,
        stages: tuple[Stage, ...] = (),
    ) -> Job:
        """Queue a job and return it immediately."""
        job_id = uuid.uuid4().hex[:12]
        job = Job(
            id=job_id,
            kind=kind,
            title=title,
            reporter=ProgressReporter(job_id, stages=stages),
            cancellation=CancellationToken(),
            function=function,
            project_id=project_id,
        )

        with self._lock:
            self._jobs[job_id] = job
            self._prune_locked()

        self._queue.put(job_id)
        logger.info("job queued", extra={"job_id": job_id, "stage": kind.value})
        return job

    # -- queries ----------------------------------------------------------

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise NotFoundError(
                f"No job with the ID {job_id!r} exists.",
                context={"job_id": job_id},
            )
        return job

    def list(self) -> tuple[Job, ...]:
        """Every known job, newest first."""
        with self._lock:
            return tuple(sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True))

    def cancel(self, job_id: str) -> Job:
        """Request cancellation.

        A queued job is cancelled immediately and never runs; a running one is
        asked to stop at its next safe point.
        """
        job = self.get(job_id)
        if job.status.is_terminal:
            return job

        job.cancellation.cancel()
        if job.status is JobStatus.QUEUED:
            job.status = JobStatus.CANCELLED
            job.finished_at = time.time()
            job.reporter.job_finished(JobStatus.CANCELLED)

        logger.info("job cancellation requested", extra={"job_id": job_id})
        return job

    def _prune_locked(self) -> None:
        """Drop the oldest finished jobs. Caller must hold the lock."""
        finished = sorted(
            (job for job in self._jobs.values() if job.status.is_terminal),
            key=lambda job: job.finished_at or job.created_at,
        )
        excess = len(finished) - self._history_limit
        for job in finished[:excess]:
            self._jobs.pop(job.id, None)

    # -- the worker -------------------------------------------------------

    def _work(self) -> None:
        while not self._shutdown.is_set():
            try:
                job_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if not job_id or self._shutdown.is_set():
                self._queue.task_done()
                continue

            with self._lock:
                job = self._jobs.get(job_id)

            if job is None or job.status is not JobStatus.QUEUED:
                # Cancelled before it ever started.
                self._queue.task_done()
                continue

            self._run(job)
            self._queue.task_done()

    def _run(self, job: Job) -> None:
        job.status = JobStatus.RUNNING
        job.started_at = time.time()

        try:
            job.result = job.function(job.reporter, job.cancellation)
            job.status = JobStatus.COMPLETED
        except CancelledError:
            job.status = JobStatus.CANCELLED
            logger.info("job cancelled", extra={"job_id": job.id})
        except DabujError as exc:
            job.status = JobStatus.FAILED
            job.error = exc.to_payload()
            logger.error("job failed", exc_info=True, extra={"job_id": job.id})
        except Exception as exc:  # noqa: BLE001 - a worker must never die
            job.status = JobStatus.FAILED
            job.error = {
                "code": "internal_error",
                "summary": "Something went wrong inside Dabuj.",
                "reason": f"{type(exc).__name__}: {exc}",
                "suggestions": ["This is a bug -- please report it with the log file"],
            }
            logger.critical("job crashed", exc_info=True, extra={"job_id": job.id})
        finally:
            job.finished_at = time.time()
            # The pipeline emits its own terminal event on the paths it
            # handles; this covers the ones it does not (e.g. model installs).
            if job.reporter.status is not job.status:
                job.reporter.job_finished(job.status, error=job.error)


__all__ = ["Job", "JobFunction", "JobKind", "JobManager"]
