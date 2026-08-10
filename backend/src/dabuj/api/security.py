"""Local-service hardening.

Binding to ``127.0.0.1`` stops other machines reaching Dabuj, but it does *not*
stop the browser the user is already running. Any web page they visit can issue
requests to ``http://127.0.0.1:7860``. Two attacks follow from that, and this
module blocks both.

**Cross-site requests.** ``evil.example`` can POST to our API from the user's
browser. Simple requests are not preflighted, so CORS alone does not prevent
the request being *made* -- it only stops the attacker reading the reply. That
is not enough when the request itself is the damage (deleting a project,
starting a job). So Dabuj rejects any request carrying an ``Origin`` header
that is not one of its own, before routing.

**DNS rebinding.** An attacker resolves their domain to ``127.0.0.1``, so the
browser believes ``evil.example`` *is* the local origin and sends no ``Origin``
header at all. Checking ``Host`` defeats this: a rebound request arrives with
``Host: evil.example``, not ``localhost``.

What is deliberately *not* here: ``Access-Control-Allow-Origin: *``. With a
wildcard, every page on the internet could read the user's transcripts.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

#: Hostnames that legitimately address a local service.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})


def allowed_origins(host: str, port: int) -> list[str]:
    """The origins the browser UI may legitimately use.

    Both ``localhost`` and ``127.0.0.1`` are included because users type either
    one, and a Vite dev server runs on its own port during development.
    """
    origins: list[str] = []
    for name in ("localhost", "127.0.0.1"):
        origins.append(f"http://{name}:{port}")
        # The frontend dev server proxies to this API.
        origins.append(f"http://{name}:5173")
    if host not in _LOCAL_HOSTS:
        origins.append(f"http://{host}:{port}")
    return origins


def _hostname_of(header: str) -> str:
    """Strip the port from a Host header, keeping IPv6 brackets intact."""
    value = header.strip()
    if value.startswith("["):
        closing = value.find("]")
        return value[: closing + 1] if closing != -1 else value
    return value.split(":", 1)[0]


class LocalOriginMiddleware(BaseHTTPMiddleware):
    """Reject cross-origin and DNS-rebound requests before they reach a route."""

    def __init__(self, app: object, *, origins: list[str], allow_lan: bool = False) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._origins = frozenset(origins)
        self._allow_lan = allow_lan

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        host_header = request.headers.get("host", "")
        hostname = _hostname_of(host_header)

        # DNS rebinding: the browser thinks it is talking to the attacker's
        # domain, which happens to resolve to our loopback address.
        if not self._allow_lan and hostname and hostname not in _LOCAL_HOSTS:
            return JSONResponse(
                status_code=421,
                content={
                    "code": "bad_host",
                    "summary": "This request was not addressed to Dabuj.",
                    "reason": (
                        "Dabuj only accepts requests addressed to localhost. "
                        "This protects it from malicious web pages."
                    ),
                    "suggestions": ["Open Dabuj at http://127.0.0.1:7860"],
                },
            )

        # Cross-site request: a page on another origin is calling our API.
        origin = request.headers.get("origin")
        if origin and origin not in self._origins:
            return JSONResponse(
                status_code=403,
                content={
                    "code": "cross_origin_blocked",
                    "summary": "A website tried to talk to your local Dabuj instance.",
                    "reason": f"The request came from {origin}, which is not Dabuj.",
                    "suggestions": ["If you did not expect this, close the page that caused it"],
                },
            )

        return await call_next(request)


__all__ = ["LocalOriginMiddleware", "allowed_origins"]
