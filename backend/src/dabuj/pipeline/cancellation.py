"""Cooperative cancellation.

Long stages check the token between units of work and raise
:class:`~dabuj.errors.CancelledError` when it is set. Cancellation must leave
the project in a valid state: completed checkpoints stay, partial files go.

The token is thread-safe because the job worker runs stages on a worker thread
while the API's event loop handles the cancel request.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from dabuj.errors import CancelledError


class CancellationToken:
    """A thread-safe one-way flag meaning "stop as soon as you sensibly can".

    Cancellation is cooperative: setting the token does not interrupt running
    code. Stages must call :meth:`raise_if_cancelled` at points where stopping
    is safe.
    """

    __slots__ = ("_event", "_callbacks", "_lock")

    def __init__(self) -> None:
        self._event = threading.Event()
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        """Request cancellation and run any registered callbacks.

        Idempotent. Callbacks are invoked once, outside the lock, and a failing
        callback never prevents the others from running -- they are typically
        best-effort cleanup such as terminating a subprocess.
        """
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks = list(self._callbacks)
            self._callbacks.clear()

        for callback in callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001 - cleanup must not mask cancellation
                # Logged, not raised: a failing cleanup callback must not stop
                # the others from running or turn a cancel into a crash.
                from dabuj.logging import get_logger  # noqa: PLC0415

                get_logger(__name__).warning("cancellation callback failed", exc_info=True)

    def raise_if_cancelled(self, what: str = "The operation") -> None:
        """Raise :class:`CancelledError` if cancellation has been requested."""
        if self._event.is_set():
            raise CancelledError(what)

    def wait(self, timeout: float | None = None) -> bool:
        """Block until cancelled or ``timeout`` elapses. Returns the flag."""
        return self._event.wait(timeout)

    def on_cancel(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register cleanup to run when cancellation is requested.

        If the token is *already* cancelled the callback runs immediately, which
        closes the race where a subprocess starts just after a cancel request.

        Returns:
            A function that unregisters the callback, for use once the guarded
            resource has been released normally.
        """
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)

                def _unregister() -> None:
                    with self._lock:
                        if callback in self._callbacks:
                            self._callbacks.remove(callback)

                return _unregister

        callback()
        return lambda: None


class NullCancellationToken(CancellationToken):
    """A token that can never be cancelled.

    Used as a default so that call sites never need ``if token is not None``.
    """

    __slots__ = ()

    def cancel(self) -> None:  # noqa: D102 - deliberately does nothing
        return


__all__ = ["CancellationToken", "NullCancellationToken"]
