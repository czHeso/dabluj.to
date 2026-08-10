"""Structured logging.

Two sinks, deliberately different:

* **Console** -- terse, human readable, warnings and above by default. The CLI
  renders its own progress and results; the log should not compete with it.
* **File** -- JSON Lines, one object per record, with the contextual fields
  (``project_id``, ``job_id``, ``stage``, ``provider``) that make a bug report
  actionable.

Privacy rule (docs/PRIVACY.md): transcript text, media contents and voice
samples are never logged. Helpers here take identifiers and counts, not
content. Filenames are logged only at DEBUG level, which is opt-in.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_CONTEXT_FIELDS = ("project_id", "job_id", "stage", "provider", "model_id")

# Attributes present on every LogRecord; anything else was added by the caller
# via `extra=` and is worth serialising.
_STANDARD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonLinesFormatter(logging.Formatter):
    """Render a record as a single JSON object on one line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """Compact single-line output for the terminal.

    Tracebacks are deliberately omitted unless ``show_traceback`` is set. A
    user running ``dabuj transcribe`` must get the rendered error panel and
    nothing else -- the traceback goes to the log file, where it is useful to
    whoever is diagnosing the problem, and ``--debug`` brings it back to the
    console for whoever is developing.
    """

    def __init__(self, *, show_traceback: bool = False) -> None:
        super().__init__()
        self.show_traceback = show_traceback

    def format(self, record: logging.LogRecord) -> str:
        context = " ".join(
            f"{field}={record.__dict__[field]}"
            for field in _CONTEXT_FIELDS
            if field in record.__dict__
        )
        base = f"{record.levelname:<7} {record.getMessage()}"
        if context:
            base = f"{base}  [{context}]"
        if record.exc_info and self.show_traceback:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


class SkipUserRenderedFilter(logging.Filter):
    """Drop console records that the caller renders itself.

    The CLI logs a failure (so it reaches the log file with its traceback) and
    *also* prints a formatted error panel. Without this filter the user would
    see both: a raw ``ERROR command failed`` line and then the panel saying the
    same thing. Records marked ``extra={"user_rendered": True}`` still reach
    the file handler, which does not install this filter.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not getattr(record, "user_rendered", False)


def configure_logging(
    *,
    log_file: Path | None = None,
    debug: bool = False,
    console_level: int | None = None,
) -> None:
    """Install Dabuj's logging configuration.

    Idempotent: calling it again replaces the previous handlers, so the CLI and
    the API server can each configure logging without stacking duplicates.

    Args:
        log_file: Where to write the JSON Lines log. Rotated at 5 MB, 3 backups.
            ``None`` disables file logging (used in tests).
        debug: Enable DEBUG level everywhere. This makes Dabuj log filenames and
            provider internals, so it is off unless the user opts in.
        console_level: Override the console threshold. Defaults to WARNING, or
            DEBUG when ``debug`` is set.
    """
    root = logging.getLogger("dabuj")
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    # Dabuj owns its own logger tree; never leak into the application root.
    root.propagate = False

    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler(stream=sys.stderr)
    console.setLevel(
        console_level
        if console_level is not None
        else (logging.DEBUG if debug else logging.WARNING)
    )
    console.setFormatter(ConsoleFormatter(show_traceback=debug))
    if not debug:
        console.addFilter(SkipUserRenderedFilter())
    root.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG if debug else logging.INFO)
        file_handler.setFormatter(JsonLinesFormatter())
        root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """Return a logger inside the ``dabuj`` tree.

    Args:
        name: Usually ``__name__``. A leading ``dabuj.`` is not duplicated.
    """
    if name == "dabuj" or name.startswith("dabuj."):
        return logging.getLogger(name)
    return logging.getLogger(f"dabuj.{name}")


__all__ = [
    "ConsoleFormatter",
    "JsonLinesFormatter",
    "SkipUserRenderedFilter",
    "configure_logging",
    "get_logger",
]
