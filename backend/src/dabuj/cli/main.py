"""The ``dabuj`` command-line interface.

Every command is a thin shell: parse arguments, call an application service,
render the result. No processing logic lives here, which is what guarantees the
CLI and the web UI behave identically.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from dabuj.application.context import AppContext
from dabuj.application.services import (
    DiagnosticsService,
    ModelService,
    ProjectService,
    TranscriptionService,
)
from dabuj.cli.render import (
    console,
    render_checks,
    render_error,
    render_media_info,
    render_model_table,
    render_system_info,
    render_unexpected_error,
)
from dabuj.domain.media import format_timestamp
from dabuj.domain.quality import Device, Precision
from dabuj.errors import CancelledError, DabujError
from dabuj.export.formats import ExportFormat
from dabuj.logging import configure_logging, get_logger
from dabuj.models.catalog import ModelTask
from dabuj.models.download import DownloadProgress
from dabuj.pipeline.cancellation import CancellationToken
from dabuj.pipeline.progress import ProgressEvent, ProgressReporter
from dabuj.pipeline.transcribe import TRANSCRIPTION_STAGES
from dabuj.version import __version__

logger = get_logger(__name__)

app = typer.Typer(
    name="dabuj",
    help="Local-first AI transcription, translation and dubbing studio.",
    no_args_is_help=True,
    add_completion=False,
)
models_app = typer.Typer(name="models", help="Manage AI models.", no_args_is_help=True)
projects_app = typer.Typer(name="projects", help="Manage projects.", no_args_is_help=True)
app.add_typer(models_app)
app.add_typer(projects_app)


@dataclass
class _CliState:
    """Process-wide state established by the root callback.

    A typed object rather than a loose dict, so the commands that read it are
    checked rather than casting at every use.
    """

    context: AppContext | None = None
    data_dir: Path | None = None
    debug: bool = False


_state = _CliState()


def _context() -> AppContext:
    """The application context, built on first use."""
    if _state.context is None:
        _state.context = AppContext.create(data_dir=_state.data_dir)
    return _state.context


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"Dabuj {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable verbose debug logging."),
    data_dir: Path | None = typer.Option(
        None, "--data-dir", help="Override the application data directory."
    ),
) -> None:
    """Configure logging and storage before any command runs."""
    _state.debug = debug
    _state.data_dir = data_dir

    context = AppContext.create(data_dir=data_dir)
    _state.context = context
    configure_logging(
        log_file=context.paths.log_file,
        debug=debug or context.settings.debug_logging,
    )


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


@app.command()
def probe(
    file: Path = typer.Argument(..., help="Media file to inspect."),
) -> None:
    """Show what a media file contains."""
    info = _context().ffmpeg.probe(file)
    render_media_info(info)


# ---------------------------------------------------------------------------
# transcribe
# ---------------------------------------------------------------------------


@app.command()
def transcribe(
    file: Path | None = typer.Argument(None, help="Media file to transcribe."),
    project_id: str | None = typer.Option(
        None, "--project", "-p", help="Resume or re-run an existing project instead."
    ),
    language: str | None = typer.Option(
        None, "--language", "-l", help="Source language code, or 'auto' to detect it."
    ),
    model: str | None = typer.Option(None, "--model", "-m", help="ASR model ID."),
    device: str | None = typer.Option(
        None, "--device", "-d", help="Compute device: auto, cpu or cuda."
    ),
    precision: str | None = typer.Option(
        None, "--precision", help="Precision: auto, int8, int8_float16, float16, float32."
    ),
    beam_size: int | None = typer.Option(
        None, "--beam-size", min=1, max=10, help="Beam width. 1 is fastest."
    ),
    no_word_timestamps: bool = typer.Option(
        False, "--no-word-timestamps", help="Skip word-level timings (slightly faster)."
    ),
    no_vad: bool = typer.Option(False, "--no-vad", help="Disable voice activity detection."),
    export_formats: str | None = typer.Option(
        "json,srt", "--export", help="Comma-separated formats to write, or 'none'."
    ),
    no_import: bool = typer.Option(
        False,
        "--no-import",
        help="Reference the media in place instead of copying it into the project.",
    ),
    force: bool = typer.Option(False, "--force", help="Ignore cached results."),
) -> None:
    """Transcribe a media file into a timestamped transcript."""
    context = _context()
    service = TranscriptionService(context)
    projects = ProjectService(context)

    if file is None and project_id is None:
        raise typer.BadParameter("Provide a media file, or --project to resume one.")

    request = service.build_request(
        language=language,
        model_id=model,
        device=Device(device) if device else None,
        precision=Precision(precision) if precision else None,
        word_timestamps=not no_word_timestamps,
        vad_filter=not no_vad,
        beam_size=beam_size,
        force=force,
    )

    _ensure_model_ready(context, request.model_id)

    cancellation = CancellationToken()
    reporter = ProgressReporter(project_id or "new", stages=TRANSCRIPTION_STAGES)

    with _stage_progress(reporter):
        try:
            if project_id is not None:
                project = projects.open(project_id)
                outcome = service.transcribe_project(
                    project, request, reporter=reporter, cancellation=cancellation
                )
            else:
                assert file is not None
                outcome = service.transcribe_file(
                    file,
                    request,
                    import_media=not no_import,
                    reporter=reporter,
                    cancellation=cancellation,
                )
        except KeyboardInterrupt:
            cancellation.cancel()
            raise

    _render_transcription_result(outcome)

    if export_formats and export_formats.lower() != "none":
        console.print()
        for name in (part.strip() for part in export_formats.split(",") if part.strip()):
            target = projects.export(outcome.project.id, ExportFormat.parse(name))
            console.print(f"  [green]✓[/green] {target}")

    console.print()
    console.print(f"[dim]Project ID: {outcome.project.id}[/dim]")


def _ensure_model_ready(context: AppContext, model_id: str | None) -> None:
    """Offer to install the ASR model if it is missing.

    Dabuj never downloads a large model silently: the user is shown the exact
    size and must confirm.
    """
    service = ModelService(context)

    if model_id is None:
        from dabuj.models.catalog import default_model_for  # noqa: PLC0415

        spec = default_model_for(ModelTask.ASR, context.recommendation.profile)
        if spec is None:
            return
        model_id = spec.id

    if context.models.is_installed(model_id):
        return

    status = service.status(model_id)
    spec = status.spec

    console.print(f"The model [bold]{spec.name}[/bold] ({spec.id}) is needed but not installed.")
    plan = service.plan_install(model_id)
    console.print(
        f"  Download size: [bold]{plan.total_bytes / 1024**2:.0f} MB[/bold] "
        f"in {len(plan.files)} file(s)"
    )
    console.print(f"  License: {spec.license}  •  Source: {spec.repo_id}")

    if not typer.confirm("Download it now?", default=True):
        raise typer.Exit(code=1)

    cancellation = CancellationToken()
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Downloading {spec.name}", total=plan.total_bytes)

        def _on_progress(event: DownloadProgress) -> None:
            progress.update(
                task,
                completed=event.downloaded_bytes,
                description=f"Downloading {event.current_file}",
            )

        service.install(model_id, plan=plan, on_progress=_on_progress, cancellation=cancellation)

    console.print(f"[green]✓[/green] {spec.name} installed.\n")


def _stage_progress(reporter: ProgressReporter) -> Progress:
    """A Rich progress display driven by pipeline events."""
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    )
    task = progress.add_task("Starting", total=1000)

    def _on_event(event: ProgressEvent) -> None:
        label = event.stage.label if event.stage else "Working"
        if event.message and event.stage:
            label = f"{label} — {event.message}"
        progress.update(
            task,
            description=label,
            completed=int((event.overall_progress or 0.0) * 1000),
        )

    reporter.subscribe(_on_event)
    return progress


def _render_transcription_result(outcome: object) -> None:
    """Print a summary of what transcription produced."""
    from dabuj.pipeline.transcribe import TranscriptionOutcome  # noqa: PLC0415

    assert isinstance(outcome, TranscriptionOutcome)
    transcript = outcome.transcript

    console.print()
    console.print(f"[green]✓[/green] Transcribed [bold]{outcome.project.name}[/bold]")
    console.print(f"  Segments: {len(transcript.segments)}")
    if transcript.duration:
        console.print(f"  Duration: {format_timestamp(transcript.duration)}")
    if outcome.detection is not None:
        detection = outcome.detection
        marker = "" if detection.is_confident else "  [yellow](low confidence)[/yellow]"
        console.print(f"  Language: {detection.language.name} ({detection.confidence:.0%}){marker}")
    if outcome.realtime_factor:
        console.print(f"  Speed: {outcome.realtime_factor:.1f}x realtime")
    if outcome.reused_stages:
        reused = ", ".join(stage.label for stage in outcome.reused_stages)
        console.print(f"  [dim]Reused from cache: {reused}[/dim]")

    for warning in outcome.warnings:
        console.print(f"  [yellow]![/yellow] {warning}")


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@app.command()
def export(
    project_id: str = typer.Argument(..., help="Project ID."),
    export_format: str = typer.Option("srt", "--format", "-f", help="json, srt, vtt or txt."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Destination file."),
    language: str | None = typer.Option(
        None, "--language", help="Export a translation instead of the source text."
    ),
    speakers: bool = typer.Option(False, "--speakers", help="Label cues with speaker names."),
) -> None:
    """Export a project's transcript."""
    target = ProjectService(_context()).export(
        project_id,
        ExportFormat.parse(export_format),
        destination=output,
        language=language,
        include_speakers=speakers,
    )
    console.print(f"[green]✓[/green] {target}")


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------


@projects_app.command("list")
def projects_list() -> None:
    """List local projects."""
    from rich.table import Table  # noqa: PLC0415

    projects = ProjectService(_context()).list()
    if not projects:
        console.print("[dim]No projects yet. Create one with: dabuj transcribe <file>[/dim]")
        return

    table = Table(box=None, padding=(0, 2, 0, 0))
    table.add_column("ID", style="bold")
    table.add_column("Name")
    table.add_column("Segments", justify="right")
    table.add_column("Language")
    table.add_column("Stages")

    for project in projects:
        document = project.document
        table.add_row(
            document.id,
            document.name,
            str(len(document.transcript.segments)),
            document.transcript.language or document.settings.source_language,
            ", ".join(stage.label for stage in document.completed_stages) or "-",
        )
    console.print(table)


@projects_app.command("delete")
def projects_delete(
    project_id: str = typer.Argument(..., help="Project ID."),
    keep_source: bool = typer.Option(
        False, "--keep-source", help="Keep the imported media, delete only derived data."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
) -> None:
    """Delete a project."""
    service = ProjectService(_context())
    project = service.open(project_id)

    if not yes and not typer.confirm(
        f"Delete the project {project.name!r} and everything in it?", default=False
    ):
        raise typer.Exit(code=1)

    service.delete(project_id, keep_source=keep_source)
    console.print(f"[green]✓[/green] Deleted {project.name}")


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


@models_app.command("list")
def models_list(
    task: str | None = typer.Option(None, "--task", help="Filter by task, e.g. asr."),
) -> None:
    """List available and installed models."""
    statuses = ModelService(_context()).list(ModelTask(task) if task else None)
    render_model_table(statuses)


@models_app.command("install")
def models_install(
    model_id: str = typer.Argument(..., help="Model ID from `dabuj models list`."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    force: bool = typer.Option(False, "--force", help="Reinstall if already present."),
) -> None:
    """Download and install a model."""
    context = _context()
    service = ModelService(context)
    status = service.status(model_id)

    if status.is_installed and not force:
        console.print(f"[green]✓[/green] {status.spec.name} is already installed.")
        return

    plan = service.plan_install(model_id)
    spec = plan.spec

    console.print(f"[bold]{spec.name}[/bold] ({spec.id})")
    console.print(f"  {spec.description}")
    console.print(
        f"  Download: [bold]{plan.total_bytes / 1024**2:.0f} MB[/bold] in {len(plan.files)} file(s)"
    )
    console.print(f"  Source:   {spec.repo_id}")
    console.print(
        f"  License:  {spec.license}"
        f"{'' if spec.commercial_use else ' (check terms before commercial use)'}"
    )
    if plan.verifiable_bytes < plan.total_bytes:
        console.print(
            "  [dim]Note: some files have no published checksum and are "
            "verified by size only.[/dim]"
        )
    console.print()

    if not yes and not typer.confirm("Download now?", default=True):
        raise typer.Exit(code=1)

    cancellation = CancellationToken()
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        bar = progress.add_task("Downloading", total=plan.total_bytes)

        def _on_progress(event: DownloadProgress) -> None:
            progress.update(bar, completed=event.downloaded_bytes, description=event.current_file)

        installed = service.install(
            model_id, plan=plan, on_progress=_on_progress, cancellation=cancellation, force=force
        )

    console.print(
        f"[green]✓[/green] Installed {installed.name} "
        f"({installed.size_bytes / 1024**2:.0f} MB) to {installed.path}"
    )


@models_app.command("remove")
def models_remove(
    model_id: str = typer.Argument(..., help="Model ID."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Remove an installed model."""
    service = ModelService(_context())
    status = service.status(model_id)

    if not status.is_installed:
        console.print(f"[dim]{model_id} is not installed.[/dim]")
        return

    if not yes and not typer.confirm(f"Remove {status.spec.name}?", default=False):
        raise typer.Exit(code=1)

    service.remove(model_id)
    console.print(f"[green]✓[/green] Removed {status.spec.name}")


# ---------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------


@app.command("system-info")
def system_info(
    as_json: bool = typer.Option(False, "--json", help="Output the report as JSON."),
) -> None:
    """Show hardware details and the recommended quality profile."""
    context = _context()
    report = DiagnosticsService(context).system_report()

    if as_json:
        import json  # noqa: PLC0415

        console.print_json(json.dumps(report))
        return

    render_system_info(
        context.system,
        context.recommendation,
        {
            "version": __version__,
            "ffmpeg": context.ffmpeg.version(),
            "models_dir": str(context.paths.models_dir),
        },
    )


@app.command()
def doctor() -> None:
    """Check that Dabuj is correctly installed and configured."""
    results = DiagnosticsService(_context()).run_checks()
    if not render_checks(results):
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@app.command()
def start(
    host: str | None = typer.Option(None, "--host", help="Bind address."),
    port: int | None = typer.Option(None, "--port", help="Port to listen on."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open a browser."),
) -> None:
    """Start the local web application."""
    from dabuj.api.server import serve  # noqa: PLC0415

    context = _context()
    serve(
        context,
        host=host,
        port=port,
        open_browser=not no_browser and context.settings.server.open_browser,
    )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run() -> None:
    """Console-script entry point.

    Turns handled errors into a clean message and exit code 1, cancellation
    into 130 (the conventional SIGINT status), and anything unexpected into a
    bug report prompt -- never a raw traceback on the user's terminal.
    """
    log_file: str | None = None
    try:
        app()
    except (CancelledError, KeyboardInterrupt):
        console.print("\n[yellow]Cancelled.[/yellow]")
        sys.exit(130)
    except DabujError as exc:
        if _state.context is not None:
            log_file = str(_state.context.paths.log_file)
        logger.error(
            "command failed",
            exc_info=True,
            extra={"code": exc.code, "user_rendered": True},
        )
        render_error(exc, log_file=log_file)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 - the last line of defence
        if _state.context is not None:
            log_file = str(_state.context.paths.log_file)
        logger.critical("unhandled error", exc_info=True, extra={"user_rendered": True})
        if _state.debug:
            raise
        render_unexpected_error(exc, log_file=log_file)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    run()
