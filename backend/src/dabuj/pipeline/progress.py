"""Progress reporting.

One event shape serves the CLI's progress bar and the WebSocket feed, so the
two can never disagree about what a job is doing.

On ETA (docs/ARCHITECTURE.md): Dabuj shows an estimate only once it has enough
evidence for one to be meaningful. A number that swings from "3 minutes" to
"2 hours" is worse than no number, so :meth:`StageProgress.eta_seconds` stays
``None`` until a minimum share of the work is done.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dabuj.pipeline.stages import Stage, StageState

#: Fraction of a stage that must complete before an ETA is trustworthy.
_MIN_FRACTION_FOR_ETA = 0.05
#: Seconds a stage must have been running before an ETA is trustworthy.
_MIN_ELAPSED_FOR_ETA = 5.0


class JobStatus(str, Enum):
    """Lifecycle of a whole job."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)


class StageProgress(BaseModel):
    """Progress within a single stage."""

    model_config = ConfigDict(validate_assignment=True)

    stage: Stage
    state: StageState = StageState.PENDING
    #: Completion in ``[0, 1]``. ``None`` when the stage cannot measure itself.
    fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    message: str | None = None
    current_item: int | None = Field(default=None, ge=0)
    total_items: int | None = Field(default=None, ge=0)
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def elapsed_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        return (self.finished_at or time.monotonic()) - self.started_at

    @property
    def eta_seconds(self) -> float | None:
        """Remaining seconds, or ``None`` if no honest estimate is possible."""
        elapsed = self.elapsed_seconds
        if (
            self.state is not StageState.RUNNING
            or self.fraction is None
            or elapsed is None
            or self.fraction < _MIN_FRACTION_FOR_ETA
            or elapsed < _MIN_ELAPSED_FOR_ETA
        ):
            return None
        return elapsed * (1.0 - self.fraction) / self.fraction


class ProgressEvent(BaseModel):
    """A single progress notification.

    Serialised straight to JSON for the WebSocket feed. Deliberately carries no
    transcript text: progress must not leak content into logs or browser
    consoles (docs/PRIVACY.md).
    """

    model_config = ConfigDict(validate_assignment=True)

    job_id: str
    status: JobStatus
    stage: Stage | None = None
    stage_state: StageState | None = None
    #: Completion of the current stage, in ``[0, 1]``.
    progress: float | None = Field(default=None, ge=0.0, le=1.0)
    #: Completion of the whole job, in ``[0, 1]``.
    overall_progress: float | None = Field(default=None, ge=0.0, le=1.0)
    message: str | None = None
    current_item: int | None = None
    total_items: int | None = None
    eta_seconds: float | None = None
    elapsed_seconds: float | None = None
    #: Realtime factor: audio seconds processed per wall-clock second.
    realtime_factor: float | None = None
    device: str | None = None
    model_id: str | None = None
    error: dict[str, Any] | None = None
    timestamp: float = Field(default_factory=time.time)


ProgressCallback = Callable[[ProgressEvent], None]
"""Anything that consumes progress events: a CLI bar, a WebSocket broadcaster."""


class ProgressReporter:
    """Collects stage progress and emits events to subscribers.

    Thread-safe: stages run on a worker thread while subscribers are typically
    driven from the event loop. Subscriber exceptions are swallowed and logged
    rather than being allowed to kill a running job -- a browser disconnecting
    mid-transcription must not lose forty minutes of work.
    """

    def __init__(
        self,
        job_id: str,
        *,
        stages: tuple[Stage, ...] = (),
        callback: ProgressCallback | None = None,
    ) -> None:
        self.job_id = job_id
        self._stages = stages
        self._lock = threading.Lock()
        self._callbacks: list[ProgressCallback] = [callback] if callback else []
        self._progress: dict[Stage, StageProgress] = {
            stage: StageProgress(stage=stage) for stage in stages
        }
        self._status = JobStatus.QUEUED
        self._started_at = time.monotonic()
        self._current: Stage | None = None

    # -- subscription -----------------------------------------------------

    def subscribe(self, callback: ProgressCallback) -> Callable[[], None]:
        """Add a subscriber. Returns a function that removes it again."""
        with self._lock:
            self._callbacks.append(callback)

        def _unsubscribe() -> None:
            with self._lock:
                if callback in self._callbacks:
                    self._callbacks.remove(callback)

        return _unsubscribe

    # -- state ------------------------------------------------------------

    @property
    def status(self) -> JobStatus:
        return self._status

    @property
    def stage_progress(self) -> dict[Stage, StageProgress]:
        with self._lock:
            return {stage: progress.model_copy() for stage, progress in self._progress.items()}

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._started_at

    def overall_fraction(self) -> float | None:
        """Mean completion across the planned stages.

        Every stage is weighted equally. That is honest about being an
        approximation -- ASR usually dominates -- but it is monotonic and
        predictable, which matters more for a progress bar than precision.
        """
        if not self._stages:
            return None
        total = 0.0
        for stage in self._stages:
            progress = self._progress[stage]
            if progress.state in (StageState.COMPLETED, StageState.SKIPPED):
                total += 1.0
            elif progress.state is StageState.RUNNING and progress.fraction is not None:
                total += progress.fraction
        return total / len(self._stages)

    # -- emission ---------------------------------------------------------

    def _emit(self, event: ProgressEvent) -> None:
        with self._lock:
            callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback(event)
            except Exception:  # noqa: BLE001 - a bad subscriber must not fail the job
                from dabuj.logging import get_logger

                get_logger(__name__).warning(
                    "progress subscriber raised", exc_info=True, extra={"job_id": self.job_id}
                )

    def job_started(self) -> None:
        self._status = JobStatus.RUNNING
        self._started_at = time.monotonic()
        self._emit(ProgressEvent(job_id=self.job_id, status=JobStatus.RUNNING))

    def stage_started(self, stage: Stage, *, total_items: int | None = None) -> None:
        with self._lock:
            self._current = stage
            self._progress[stage] = StageProgress(
                stage=stage,
                state=StageState.RUNNING,
                fraction=0.0,
                total_items=total_items,
                started_at=time.monotonic(),
                message=stage.label,
            )
        self._emit(self._event_for(stage))

    def update(
        self,
        stage: Stage,
        *,
        fraction: float | None = None,
        message: str | None = None,
        current_item: int | None = None,
        total_items: int | None = None,
        realtime_factor: float | None = None,
        device: str | None = None,
        model_id: str | None = None,
    ) -> None:
        """Report progress inside a running stage."""
        with self._lock:
            progress = self._progress.get(stage) or StageProgress(stage=stage)
            updates: dict[str, Any] = {"state": StageState.RUNNING}
            if fraction is not None:
                # Clamp: providers occasionally report slightly over 1.0.
                updates["fraction"] = min(1.0, max(0.0, fraction))
            if message is not None:
                updates["message"] = message
            if current_item is not None:
                updates["current_item"] = current_item
            if total_items is not None:
                updates["total_items"] = total_items
            if progress.started_at is None:
                updates["started_at"] = time.monotonic()
            self._progress[stage] = progress.model_copy(update=updates)

        self._emit(
            self._event_for(
                stage, realtime_factor=realtime_factor, device=device, model_id=model_id
            )
        )

    def stage_finished(self, stage: Stage, *, state: StageState = StageState.COMPLETED) -> None:
        with self._lock:
            progress = self._progress.get(stage) or StageProgress(stage=stage)
            self._progress[stage] = progress.model_copy(
                update={
                    "state": state,
                    "fraction": 1.0 if state is StageState.COMPLETED else progress.fraction,
                    "finished_at": time.monotonic(),
                }
            )
        self._emit(self._event_for(stage))

    def job_finished(self, status: JobStatus, *, error: dict[str, Any] | None = None) -> None:
        self._status = status
        self._emit(
            ProgressEvent(
                job_id=self.job_id,
                status=status,
                overall_progress=1.0 if status is JobStatus.COMPLETED else self.overall_fraction(),
                elapsed_seconds=self.elapsed_seconds,
                error=error,
            )
        )

    def _event_for(
        self,
        stage: Stage,
        *,
        realtime_factor: float | None = None,
        device: str | None = None,
        model_id: str | None = None,
    ) -> ProgressEvent:
        progress = self._progress[stage]
        return ProgressEvent(
            job_id=self.job_id,
            status=self._status,
            stage=stage,
            stage_state=progress.state,
            progress=progress.fraction,
            overall_progress=self.overall_fraction(),
            message=progress.message,
            current_item=progress.current_item,
            total_items=progress.total_items,
            eta_seconds=progress.eta_seconds,
            elapsed_seconds=self.elapsed_seconds,
            realtime_factor=realtime_factor,
            device=device,
            model_id=model_id,
        )


__all__ = [
    "JobStatus",
    "ProgressCallback",
    "ProgressEvent",
    "ProgressReporter",
    "StageProgress",
]
