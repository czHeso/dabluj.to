"""Starting the local web server.

Handles the practical details of ``dabuj start``: choosing a free port,
printing the start-up summary, and opening the browser once the server is
actually listening rather than optimistically before it is.
"""

from __future__ import annotations

import socket
import threading
import webbrowser

import uvicorn

from dabuj.api.app import create_app
from dabuj.application.context import AppContext
from dabuj.errors import ConfigurationError
from dabuj.logging import get_logger
from dabuj.version import __version__

logger = get_logger(__name__)

#: How many ports to try after the preferred one before giving up.
_PORT_SEARCH_RANGE = 20


def find_free_port(host: str, preferred: int, *, search: bool = True) -> int:
    """Return a bindable port, starting from ``preferred``.

    Args:
        host: The address that will be bound.
        preferred: First choice.
        search: Try subsequent ports when the preferred one is taken.

    Raises:
        ConfigurationError: If no port in the search range is free.
    """
    candidates = range(preferred, preferred + (_PORT_SEARCH_RANGE if search else 1))

    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
            except OSError:
                continue
            return port

    raise ConfigurationError(
        f"No free port was found near {preferred}.",
        reason=(f"Ports {preferred} to {preferred + _PORT_SEARCH_RANGE - 1} are all in use."),
        suggestions=[
            "Close whatever is using them, or choose another with --port",
        ],
        context={"host": host, "preferred_port": preferred},
    )


def serve(
    context: AppContext,
    *,
    host: str | None = None,
    port: int | None = None,
    open_browser: bool = True,
) -> None:
    """Run the local web application until interrupted."""
    settings = context.settings.server
    bind_host = host or settings.effective_host
    chosen_port = find_free_port(
        bind_host if bind_host != "0.0.0.0" else "127.0.0.1",  # noqa: S104
        port or settings.port,
        search=settings.auto_port and port is None,
    )

    url = f"http://127.0.0.1:{chosen_port}"
    _print_banner(context, url, bind_host)

    if open_browser:
        _open_browser_when_ready(url, chosen_port)

    # The chosen port, not the configured one: see create_app's docstring.
    app = create_app(context, port=chosen_port)
    uvicorn.run(
        app,
        host=bind_host,
        port=chosen_port,
        log_level="warning",
        access_log=False,
    )


def _print_banner(context: AppContext, url: str, bind_host: str) -> None:
    """Print the start-up summary described in docs/ARCHITECTURE.md."""
    from dabuj.cli.render import console  # noqa: PLC0415 - avoids a cycle at import

    recommendation = context.recommendation
    gpu = context.system.primary_gpu

    console.print(f"\n[bold]Dabuj {__version__}[/bold]")
    console.print(
        f"  {'[green]✓[/green]' if context.ffmpeg.is_available else '[red]✗[/red]'} FFmpeg"
    )
    console.print(f"  [green]✓[/green] Storage  [dim]{context.paths.data_dir}[/dim]")

    if gpu is not None and recommendation.device.value != "cpu":
        vram = f", {gpu.total_memory_gib:.0f} GB VRAM" if gpu.total_memory_gib else ""
        console.print(f"  [green]✓[/green] {gpu.name}{vram}")
    else:
        console.print("  [green]✓[/green] CPU mode")

    console.print(f"  Recommended profile: [bold]{recommendation.profile.label}[/bold]")

    if context.settings.privacy.allow_cloud_providers:
        console.print(
            "\n  [yellow]Cloud providers are enabled. Data may leave this computer.[/yellow]"
        )
    else:
        console.print("\n  [green]Local processing. Your media stays on this computer.[/green]")

    if bind_host == "0.0.0.0":  # noqa: S104
        console.print(
            "\n  [yellow]![/yellow] [yellow]Dabuj is reachable from your network. "
            "There is no authentication.[/yellow]"
        )

    console.print(f"\n  Opening [bold]{url}[/bold]\n")


def _open_browser_when_ready(url: str, port: int, *, timeout: float = 15.0) -> None:
    """Open the browser once the port actually accepts connections.

    Opening immediately races the server and often lands on a connection-refused
    page, which looks like a failure to the user.
    """

    def _wait_and_open() -> None:
        import time  # noqa: PLC0415

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.5)
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    webbrowser.open(url)
                    return
            time.sleep(0.25)
        logger.warning("server did not become ready in time; not opening a browser")

    threading.Thread(target=_wait_and_open, name="dabuj-browser", daemon=True).start()


__all__ = ["find_free_port", "serve"]
