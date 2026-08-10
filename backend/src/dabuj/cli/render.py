"""Terminal rendering helpers.

Kept apart from the command definitions so that the commands stay readable and
so that presentation can be tested without invoking Typer.

The error renderer is the important one: it is what stands between a user and a
raw traceback. It prints the summary, the reason and the suggestions, and tells
the user where the technical detail was written.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from dabuj.application.services import CheckResult, ModelStatus
from dabuj.domain.media import MediaInfo, format_timestamp
from dabuj.errors import DabujError
from dabuj.hardware.detect import SystemInfo
from dabuj.hardware.profiles import ProfileRecommendation

console = Console()
error_console = Console(stderr=True)

_TICK = "✓"
_CROSS = "✗"


def render_error(exc: DabujError, *, log_file: str | None = None) -> None:
    """Print a handled error as something the user can act on."""
    body = Text()
    body.append(exc.summary, style="bold")

    if exc.reason:
        body.append("\n\n")
        body.append(exc.reason, style="dim")

    if exc.suggestions:
        body.append("\n\nTry:")
        for suggestion in exc.suggestions:
            body.append(f"\n  • {suggestion}")

    error_console.print(Panel(body, title="Error", border_style="red", padding=(1, 2)))

    if log_file:
        error_console.print(f"[dim]Technical details were written to {log_file}[/dim]")


def render_unexpected_error(exc: BaseException, *, log_file: str | None = None) -> None:
    """Print an *unhandled* exception without dumping a traceback at the user.

    An exception reaching here is a bug in Dabuj, so the message says so and
    points at the log, which does contain the traceback.
    """
    body = Text()
    body.append("Something went wrong inside Dabuj.", style="bold")
    body.append("\n\nThis is a bug, not something you did wrong.", style="dim")
    body.append(f"\n\n{type(exc).__name__}: {exc}", style="dim")
    body.append("\n\nTry:")
    body.append("\n  • Run the same command with --debug for more detail")
    body.append("\n  • Report it with the log file attached")

    error_console.print(Panel(body, title="Unexpected error", border_style="red", padding=(1, 2)))
    if log_file:
        error_console.print(f"[dim]Full traceback: {log_file}[/dim]")


def render_media_info(info: MediaInfo) -> None:
    """Print what a media file contains."""
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column(style="dim")
    table.add_column()

    table.add_row("File", info.path.name)
    table.add_row("Type", info.kind.value)
    table.add_row("Format", info.format_long_name or info.format_name or "unknown")
    table.add_row("Size", _bytes(info.size_bytes))
    if info.duration is not None:
        table.add_row("Duration", format_timestamp(info.duration))

    for index, audio in enumerate(info.audio_streams):
        details = [audio.codec or "unknown"]
        if audio.sample_rate:
            details.append(f"{audio.sample_rate} Hz")
        if audio.channels:
            details.append(f"{audio.channels} ch")
        if audio.language:
            details.append(audio.language)
        table.add_row(f"Audio {index}", ", ".join(details))

    for index, video in enumerate(info.video_streams):
        details = [video.codec or "unknown"]
        if video.resolution:
            details.append(video.resolution)
        if video.frame_rate:
            details.append(f"{video.frame_rate:.2f} fps")
        table.add_row(f"Video {index}", ", ".join(details))

    if info.subtitle_streams:
        table.add_row(
            "Subtitles",
            ", ".join(s.language or s.codec or "unknown" for s in info.subtitle_streams),
        )
    if info.chapter_count:
        table.add_row("Chapters", str(info.chapter_count))

    console.print(table)


def render_system_info(
    system: SystemInfo, recommendation: ProfileRecommendation, extra: dict[str, Any]
) -> None:
    """Print the hardware report behind ``dabuj system-info``."""
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column(style="dim")
    table.add_column()

    table.add_row("Application", f"Dabuj {extra.get('version', '')}")
    table.add_row("OS", f"{system.os_name} {system.os_version} ({system.machine})")
    table.add_row("Python", system.python_version)
    table.add_row("CPU", system.cpu.name)
    table.add_row(
        "Cores",
        f"{system.cpu.physical_cores or '?'} physical / {system.cpu.logical_cores or '?'} logical",
    )
    table.add_row(
        "Memory",
        f"{system.total_memory_gib:.1f} GB total, {system.available_memory_gib:.1f} GB available",
    )

    if system.gpus:
        for gpu in system.gpus:
            vram = f"{gpu.total_memory_gib:.1f} GB VRAM" if gpu.total_memory_gib else "VRAM unknown"
            table.add_row("GPU", f"{gpu.name} ({vram})")
    else:
        table.add_row("GPU", "none detected")

    accelerators = system.accelerators
    enabled = [
        name
        for name, on in (
            ("CUDA", accelerators.cuda),
            ("DirectML", accelerators.directml),
            ("ROCm", accelerators.rocm),
            ("Metal", accelerators.metal),
        )
        if on
    ]
    table.add_row("Acceleration", ", ".join(enabled) if enabled else "CPU only")

    table.add_row("FFmpeg", extra.get("ffmpeg") or "not found")
    table.add_row("Models directory", extra.get("models_dir", ""))
    if system.free_disk_bytes is not None:
        table.add_row("Free disk", _bytes(system.free_disk_bytes))

    console.print(table)
    console.print()
    console.print(
        f"Recommended profile: [bold]{recommendation.profile.label}[/bold] "
        f"({recommendation.device.value}, {recommendation.precision.value})"
    )
    for reason in recommendation.reasons:
        console.print(f"  [dim]{reason}[/dim]")
    for warning in recommendation.warnings:
        console.print(f"  [yellow]![/yellow] {warning}")


def render_checks(results: tuple[CheckResult, ...]) -> bool:
    """Print doctor results. Returns True when everything passed."""
    for result in results:
        mark = f"[green]{_TICK}[/green]" if result.ok else f"[red]{_CROSS}[/red]"
        console.print(f"{mark} {result.name}")
        console.print(f"   [dim]{result.detail}[/dim]")
        if result.suggestion:
            style = "yellow" if result.ok else "red"
            console.print(f"   [{style}]{result.suggestion}[/{style}]")

    failures = [r for r in results if not r.ok]
    console.print()
    if failures:
        console.print(f"[red]{len(failures)} problem(s) found.[/red]")
        return False
    console.print("[green]No problems found.[/green]")
    return True


def render_model_table(statuses: tuple[ModelStatus, ...]) -> None:
    """Print the model catalog with install state, size and license."""
    table = Table(box=None, padding=(0, 2, 0, 0))
    table.add_column("ID", style="bold")
    table.add_column("Name")
    table.add_column("Size", justify="right")
    table.add_column("License")
    table.add_column("Commercial")
    table.add_column("Status")

    for status in statuses:
        spec = status.spec
        installed = status.installed
        table.add_row(
            spec.id,
            spec.name,
            _bytes(installed.size_bytes) if installed else spec.approx_size_label,
            spec.license,
            "yes" if spec.commercial_use else "check license",
            "[green]installed[/green]" if status.is_installed else "[dim]not installed[/dim]",
        )

    console.print(table)
    console.print()
    console.print(
        "[dim]Model licenses are separate from Dabuj's MIT license. "
        "See docs/MODELS.md before commercial use.[/dim]"
    )


def _bytes(value: float) -> str:
    """Human-readable byte count."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024.0 or unit == "TB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


__all__ = [
    "console",
    "error_console",
    "render_checks",
    "render_error",
    "render_media_info",
    "render_model_table",
    "render_system_info",
    "render_unexpected_error",
]
