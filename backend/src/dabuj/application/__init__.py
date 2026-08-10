"""Application services.

This is the layer the CLI and the HTTP API both call. Neither of them contains
processing logic of its own: they translate their own input format into a
request object, hand it to a service, and render whatever comes back.

That is what keeps ``dabuj transcribe`` and ``POST /api/jobs`` genuinely
equivalent rather than two implementations that drift apart.
"""

from dabuj.application.context import AppContext
from dabuj.application.services import (
    DiagnosticsService,
    ModelService,
    ProjectService,
    TranscriptionService,
)

__all__ = [
    "AppContext",
    "DiagnosticsService",
    "ModelService",
    "ProjectService",
    "TranscriptionService",
]
