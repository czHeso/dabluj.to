"""The FastAPI application.

Routes are thin: they validate input, call an application service, and return
the result. All processing logic lives in ``dabuj.application`` and is shared
with the CLI.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from dabuj.api.schemas import (
    CreateProjectRequest,
    ExportRequest,
    InstallModelRequest,
    MergeSegmentsRequest,
    RenameSpeakerRequest,
    SegmentUpdateRequest,
    SplitSegmentRequest,
    TranscribeRequest,
)
from dabuj.api.security import LocalOriginMiddleware, allowed_origins
from dabuj.application.context import AppContext
from dabuj.application.services import (
    DiagnosticsService,
    ModelService,
    ProjectService,
    TranscriptionService,
)
from dabuj.domain.speaker import Speaker
from dabuj.errors import DabujError, NotFoundError, ValidationError
from dabuj.export.formats import ExportFormat
from dabuj.jobs.manager import JobKind, JobManager
from dabuj.logging import get_logger
from dabuj.models.catalog import ModelTask
from dabuj.pipeline.progress import ProgressEvent
from dabuj.pipeline.transcribe import TRANSCRIPTION_STAGES
from dabuj.version import __version__

logger = get_logger(__name__)

#: Where the built frontend lives, relative to the repository root.
_FRONTEND_DIST = Path(__file__).resolve().parents[4] / "frontend" / "dist"


def create_app(
    context: AppContext,
    *,
    jobs: JobManager | None = None,
    port: int | None = None,
) -> FastAPI:
    """Build the application.

    Args:
        context: The wired application context.
        jobs: A job manager. One is created and owned by the app if omitted.
        port: The port the server will actually listen on. This must be the
            *effective* port, not the configured preference: when the preferred
            port is taken, or ``--port`` overrides it, the browser's Origin
            header carries the real port, and an allow-list built from the
            configured one would reject the application's own UI.
    """
    manager = jobs or JobManager(concurrency=1)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        manager.start()
        try:
            yield
        finally:
            manager.shutdown()

    app = FastAPI(
        title="Dabuj",
        version=__version__,
        summary="Local-first AI transcription, translation and dubbing studio.",
        lifespan=lifespan,
    )

    origins = allowed_origins(context.settings.server.host, port or context.settings.server.port)
    # Order matters: the origin guard runs before CORS so a blocked request
    # never reaches a route handler.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.add_middleware(
        LocalOriginMiddleware,
        origins=origins,
        allow_lan=context.settings.server.allow_lan,
    )

    app.state.context = context
    app.state.jobs = manager

    _register_error_handlers(app)
    _register_routes(app, context, manager)
    _mount_frontend(app)

    return app


def _register_error_handlers(app: FastAPI) -> None:
    """Map domain errors onto the single wire error shape."""

    @app.exception_handler(DabujError)
    async def _handle_dabuj_error(_: Request, exc: DabujError) -> JSONResponse:
        if exc.http_status >= 500:
            logger.error("request failed", exc_info=True, extra={"code": exc.code})
        return JSONResponse(status_code=exc.http_status, content=exc.to_payload())

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.critical("unhandled request error", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                "summary": "Something went wrong inside Dabuj.",
                "reason": f"{type(exc).__name__}: {exc}",
                "suggestions": ["This is a bug -- please report it with the log file"],
                "context": {},
            },
        )


def _register_routes(app: FastAPI, context: AppContext, jobs: JobManager) -> None:
    projects = ProjectService(context)
    models = ModelService(context)
    transcription = TranscriptionService(context)
    diagnostics = DiagnosticsService(context)

    # -- system -----------------------------------------------------------

    @app.get("/api/health", tags=["system"])
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/system", tags=["system"])
    async def system() -> dict[str, Any]:
        return diagnostics.system_report()

    @app.get("/api/system/checks", tags=["system"])
    async def system_checks() -> dict[str, Any]:
        results = diagnostics.run_checks()
        return {
            "ok": all(result.ok for result in results),
            "checks": [
                {
                    "name": result.name,
                    "ok": result.ok,
                    "detail": result.detail,
                    "suggestion": result.suggestion,
                }
                for result in results
            ],
        }

    @app.get("/api/settings", tags=["system"])
    async def get_settings() -> dict[str, Any]:
        """Current settings, including the privacy state the UI displays."""
        return {
            "settings": context.settings.model_dump(mode="json"),
            "privacy": {
                "local_only": not context.settings.privacy.allow_cloud_providers,
                "telemetry": context.settings.privacy.telemetry,
            },
            "storage": {
                "models_dir": str(context.paths.models_dir),
                "projects_dir": str(context.paths.projects_dir),
            },
        }

    # -- models -----------------------------------------------------------

    @app.get("/api/models", tags=["models"])
    async def list_models(task: str | None = Query(default=None)) -> dict[str, Any]:
        statuses = models.list(ModelTask(task) if task else None)
        return {"models": [status.to_dict() for status in statuses]}

    @app.get("/api/models/{model_id}", tags=["models"])
    async def get_model(model_id: str) -> dict[str, Any]:
        return models.status(model_id).to_dict()

    @app.get("/api/models/{model_id}/plan", tags=["models"])
    async def plan_model(model_id: str) -> dict[str, Any]:
        """What installing this model would download.

        The UI shows this before asking the user to confirm; Dabuj never starts
        a multi-gigabyte transfer without it.
        """
        plan = models.plan_install(model_id)
        return {
            "model_id": plan.spec.id,
            "name": plan.spec.name,
            "total_bytes": plan.total_bytes,
            "verifiable_bytes": plan.verifiable_bytes,
            "file_count": len(plan.files),
            "license": plan.spec.license,
            "source": plan.spec.repo_id,
        }

    @app.post("/api/models/install", tags=["models"], status_code=202)
    async def install_model(request: InstallModelRequest) -> dict[str, Any]:
        status = models.status(request.model_id)
        plan = models.plan_install(request.model_id)

        def _work(reporter, cancellation):  # type: ignore[no-untyped-def]
            def _on_progress(event: Any) -> None:
                reporter.update(
                    TRANSCRIPTION_STAGES[0],
                    fraction=event.fraction,
                    message=f"Downloading {event.current_file}",
                )

            return models.install(
                request.model_id,
                plan=plan,
                on_progress=_on_progress,
                cancellation=cancellation,
                force=request.force,
            ).to_dict()

        job = jobs.submit(
            JobKind.MODEL_INSTALL,
            f"Installing {status.spec.name}",
            _work,
            stages=(TRANSCRIPTION_STAGES[0],),
        )
        return job.to_dict()

    @app.delete("/api/models/{model_id}", tags=["models"])
    async def remove_model(model_id: str) -> dict[str, Any]:
        models.remove(model_id)
        return {"removed": model_id}

    # -- projects ---------------------------------------------------------

    @app.get("/api/projects", tags=["projects"])
    async def list_projects() -> dict[str, Any]:
        return {
            "projects": [
                {
                    "id": project.id,
                    "name": project.name,
                    "created_at": project.document.created_at,
                    "updated_at": project.document.updated_at,
                    "source_filename": project.document.source.original_filename,
                    "segment_count": len(project.document.transcript.segments),
                    "language": project.document.transcript.language,
                    "completed_stages": [
                        stage.value for stage in project.document.completed_stages
                    ],
                }
                for project in projects.list()
            ]
        }

    @app.post("/api/projects", tags=["projects"], status_code=201)
    async def create_project(request: CreateProjectRequest) -> dict[str, Any]:
        source = _validated_local_path(request.source_path)
        project = projects.create(
            source,
            name=request.name,
            source_language=request.source_language,
            target_language=request.target_language,
            quality=request.quality,
            asr_model=request.asr_model,
            import_media=request.import_media,
        )
        return project.document.model_dump(mode="json")

    @app.get("/api/projects/{project_id}", tags=["projects"])
    async def get_project(project_id: str) -> dict[str, Any]:
        return projects.open(project_id).document.model_dump(mode="json")

    @app.delete("/api/projects/{project_id}", tags=["projects"])
    async def delete_project(
        project_id: str, keep_source: bool = Query(default=False)
    ) -> dict[str, Any]:
        projects.delete(project_id, keep_source=keep_source)
        return {"deleted": project_id}

    @app.get("/api/projects/{project_id}/transcript", tags=["transcript"])
    async def get_transcript(project_id: str) -> dict[str, Any]:
        project = projects.open(project_id)
        return {
            "transcript": project.document.transcript.model_dump(mode="json"),
            "speakers": {
                key: speaker.model_dump(mode="json")
                for key, speaker in project.document.speakers.items()
            },
        }

    # -- transcript editing ----------------------------------------------

    @app.patch("/api/projects/{project_id}/segments/{segment_id}", tags=["transcript"])
    async def update_segment(
        project_id: str, segment_id: str, request: SegmentUpdateRequest
    ) -> dict[str, Any]:
        project = projects.open(project_id)
        transcript = project.document.transcript
        segment = transcript.find(segment_id)
        if segment is None:
            raise NotFoundError(
                f"No segment with the ID {segment_id!r} exists in this project.",
                context={"project_id": project_id, "segment_id": segment_id},
            )

        if request.text is not None:
            segment = segment.with_edit(request.text)
        updates: dict[str, Any] = {}
        if request.speaker_id is not None:
            updates["speaker_id"] = request.speaker_id
        if request.start is not None:
            updates["start"] = request.start
        if request.end is not None:
            updates["end"] = request.end
        if updates:
            candidate_end = updates.get("end", segment.end)
            candidate_start = updates.get("start", segment.start)
            if candidate_end < candidate_start:
                raise ValidationError(
                    "A segment cannot end before it starts.",
                    context={"segment_id": segment_id},
                )
            segment = segment.model_copy(update=updates)

        project.document.transcript = transcript.replace_segment(segment)
        context.projects.save(project)
        return segment.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/segments/{segment_id}/split", tags=["transcript"])
    async def split_segment(
        project_id: str, segment_id: str, request: SplitSegmentRequest
    ) -> dict[str, Any]:
        project = projects.open(project_id)
        project.document.transcript = project.document.transcript.split_segment(
            segment_id, request.timestamp
        )
        context.projects.save(project)
        return project.document.transcript.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/segments/merge", tags=["transcript"])
    async def merge_segments(project_id: str, request: MergeSegmentsRequest) -> dict[str, Any]:
        project = projects.open(project_id)
        project.document.transcript = project.document.transcript.merge_segments(
            request.first_id, request.second_id
        )
        context.projects.save(project)
        return project.document.transcript.model_dump(mode="json")

    @app.patch("/api/projects/{project_id}/speakers/{speaker_id}", tags=["transcript"])
    async def rename_speaker(
        project_id: str, speaker_id: str, request: RenameSpeakerRequest
    ) -> dict[str, Any]:
        project = projects.open(project_id)
        speaker = project.document.speakers.get(speaker_id) or Speaker(id=speaker_id)
        project.document.speakers[speaker_id] = speaker.renamed(request.display_name)
        context.projects.save(project)
        return project.document.speakers[speaker_id].model_dump(mode="json")

    # -- export -----------------------------------------------------------

    @app.post("/api/projects/{project_id}/export", tags=["export"])
    async def export_project(project_id: str, request: ExportRequest) -> dict[str, Any]:
        target = projects.export(
            project_id,
            ExportFormat.parse(request.format),
            language=request.language,
            include_speakers=request.include_speakers,
        )
        return {"path": str(target), "format": request.format, "bytes": target.stat().st_size}

    @app.get("/api/projects/{project_id}/export/{export_format}", tags=["export"])
    async def download_export(project_id: str, export_format: str) -> FileResponse:
        fmt = ExportFormat.parse(export_format)
        target = projects.export(project_id, fmt)
        return FileResponse(target, filename=target.name, media_type="application/octet-stream")

    # -- jobs -------------------------------------------------------------

    @app.get("/api/jobs", tags=["jobs"])
    async def list_jobs() -> dict[str, Any]:
        return {"jobs": [job.to_dict() for job in jobs.list()]}

    @app.get("/api/jobs/{job_id}", tags=["jobs"])
    async def get_job(job_id: str) -> dict[str, Any]:
        return jobs.get(job_id).to_dict()

    @app.post("/api/jobs/{job_id}/cancel", tags=["jobs"])
    async def cancel_job(job_id: str) -> dict[str, Any]:
        return jobs.cancel(job_id).to_dict()

    @app.post("/api/jobs/transcribe", tags=["jobs"], status_code=202)
    async def start_transcription(request: TranscribeRequest) -> dict[str, Any]:
        project = projects.open(request.project_id)
        pipeline_request = transcription.build_request(
            language=request.language,
            model_id=request.model_id,
            device=request.device,
            precision=request.precision,
            word_timestamps=request.word_timestamps,
            vad_filter=request.vad_filter,
            beam_size=request.beam_size,
            force=request.force,
        )

        def _work(reporter, cancellation):  # type: ignore[no-untyped-def]
            outcome = transcription.transcribe_project(
                project, pipeline_request, reporter=reporter, cancellation=cancellation
            )
            return {
                "project_id": outcome.project.id,
                "segment_count": len(outcome.transcript.segments),
            }

        job = jobs.submit(
            JobKind.TRANSCRIBE,
            f"Transcribing {project.name}",
            _work,
            project_id=project.id,
            stages=TRANSCRIPTION_STAGES,
        )
        return job.to_dict()

    # -- websocket --------------------------------------------------------

    @app.websocket("/ws/jobs/{job_id}")
    async def job_events(websocket: WebSocket, job_id: str) -> None:
        """Stream a job's progress.

        Events arrive on the pipeline's worker thread, so they are handed to
        the event loop through a thread-safe queue rather than being awaited
        from the wrong thread.
        """
        await websocket.accept()

        try:
            job = jobs.get(job_id)
        except NotFoundError as exc:
            await websocket.send_json(exc.to_payload())
            await websocket.close(code=1008)
            return

        loop = asyncio.get_running_loop()
        events: asyncio.Queue[ProgressEvent] = asyncio.Queue()

        def _on_event(event: ProgressEvent) -> None:
            loop.call_soon_threadsafe(events.put_nowait, event)

        unsubscribe = job.reporter.subscribe(_on_event)

        try:
            await websocket.send_json({"type": "snapshot", "job": job.to_dict()})

            while True:
                if job.status.is_terminal and events.empty():
                    await websocket.send_json({"type": "final", "job": job.to_dict()})
                    break
                try:
                    event = await asyncio.wait_for(events.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                await websocket.send_json(
                    {"type": "progress", "event": event.model_dump(mode="json")}
                )
        except WebSocketDisconnect:
            pass
        finally:
            unsubscribe()
            with contextlib.suppress(RuntimeError):
                await websocket.close()

    # -- helpers ----------------------------------------------------------

    def _validated_local_path(raw: str) -> Path:
        """Validate a user-supplied local media path.

        The browser cannot send a real filesystem path for a dropped file, so
        this endpoint exists for the *advanced* local-path workflow, where the
        user types or pastes a path. It is still validated: it must exist and
        be a regular file. It is deliberately not confined to a sandbox --
        the whole point is to open the user's own media in place -- but the
        origin guard in ``security.py`` is what prevents a web page reaching
        this endpoint at all.
        """
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            raise ValidationError(
                "An absolute path is required.",
                reason=(
                    f"{raw!r} is relative, and the server has no notion of your working directory."
                ),
                context={"path": raw},
            )
        if not candidate.is_file():
            raise ValidationError(
                "That file does not exist.",
                reason=f"Nothing was found at {candidate}.",
                suggestions=["Check the path and try again"],
                context={"path": str(candidate)},
            )
        return candidate


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built React application, if it has been built.

    Absent in a source checkout that has not run ``npm run build``; the API
    still works, which is what the tests and the CLI rely on.
    """
    if not _FRONTEND_DIST.is_dir():
        logger.info("frontend build not found; serving the API only")

        @app.get("/", include_in_schema=False)
        async def _no_frontend() -> JSONResponse:
            return JSONResponse(
                {
                    "summary": "The Dabuj web interface has not been built.",
                    "reason": "No production build was found in frontend/dist.",
                    "suggestions": ["Run: cd frontend && npm install && npm run build"],
                }
            )

        return

    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")


__all__ = ["create_app"]
