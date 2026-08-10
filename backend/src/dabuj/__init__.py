"""Dabuj -- a local-first AI transcription, translation and dubbing studio.

The public surface of this package is deliberately small. Application code
should import from the focused subpackages (``dabuj.domain``, ``dabuj.media``,
``dabuj.application`` and so on) rather than relying on re-exports here.
"""

from dabuj.version import APP_NAME, __version__

__all__ = ["__version__", "APP_NAME"]
