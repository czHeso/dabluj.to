"""Outbound HTTPS configuration.

Dabuj makes network requests for exactly one thing: downloading models the user
has explicitly asked for. That one path still has to work on real machines,
which is why this module exists.

**The problem.** Python's HTTP libraries verify certificates against the
``certifi`` CA bundle, which contains only the public root CAs. It does *not*
contain the private root CA that a corporate TLS-inspecting proxy, a captive
network appliance, or an antivirus product with HTTPS scanning installs into
the operating system's trust store. On such a machine every HTTPS request from
Python fails with ``CERTIFICATE_VERIFY_FAILED`` while the user's browser works
perfectly -- which makes the failure look like a Dabuj bug.

**The fix.** Verify against the *operating system's* trust store, which is
where the administrator or the security product actually installed its root.
That is what the browser does, and it is the correct source of truth on a
desktop machine.

**What this is not.** Verification is never disabled. There is no
``verify=False`` anywhere in Dabuj and no setting to turn certificate checking
off. A machine whose OS trust store rejects the certificate gets an error, as
it should.
"""

from __future__ import annotations

import ssl
from typing import cast

from dabuj.logging import get_logger

logger = get_logger(__name__)

_cached_context: ssl.SSLContext | None = None


def create_ssl_context() -> ssl.SSLContext:
    """Build an SSL context that trusts what the operating system trusts.

    Falls back to Python's default (certifi) if ``truststore`` is unavailable
    or cannot be initialised, so this can only ever improve on the default --
    never break a machine that already worked.

    Returns:
        A verifying :class:`ssl.SSLContext`. Certificate and hostname checking
        are always on.
    """
    global _cached_context  # noqa: PLW0603 - a process-wide cache is the point
    if _cached_context is not None:
        return _cached_context

    context: ssl.SSLContext
    try:
        import truststore  # noqa: PLC0415

        # truststore.SSLContext is a drop-in ssl.SSLContext at runtime, but is
        # not declared as a subclass, hence the cast.
        context = cast(ssl.SSLContext, truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
        logger.debug("using the operating system trust store for HTTPS")
    except Exception:  # noqa: BLE001 - any failure falls back to the default
        context = ssl.create_default_context()
        logger.debug("using the certifi trust store for HTTPS")

    # Belt and braces: these are the defaults, but an explicit assertion here
    # means a future refactor cannot silently weaken them.
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED

    _cached_context = context
    return context


def reset_ssl_context() -> None:
    """Discard the cached context. For tests only."""
    global _cached_context  # noqa: PLW0603
    _cached_context = None


__all__ = ["create_ssl_context", "reset_ssl_context"]
