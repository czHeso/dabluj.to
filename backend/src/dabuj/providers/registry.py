"""Provider lookup.

Maps a provider name from the model catalog to an implementation. Providers are
constructed lazily and their runtimes imported inside ``load()``, so listing the
catalog on a machine with no ML dependencies installed costs nothing and fails
nowhere.
"""

from __future__ import annotations

from collections.abc import Callable

from dabuj.errors import ProviderUnavailableError
from dabuj.models.catalog import ModelTask
from dabuj.providers.asr.base import ASRProvider


def _make_faster_whisper() -> ASRProvider:
    from dabuj.providers.asr.faster_whisper_provider import (  # noqa: PLC0415
        FasterWhisperProvider,
    )

    return FasterWhisperProvider()


#: Provider name -> factory. Adding a backend means adding one line here and
#: one module; nothing else in the application changes.
_ASR_PROVIDERS: dict[str, Callable[[], ASRProvider]] = {
    "faster_whisper": _make_faster_whisper,
}


class ProviderRegistry:
    """Resolves provider names to implementations, per task."""

    def __init__(self) -> None:
        self._asr = dict(_ASR_PROVIDERS)

    def register_asr(self, name: str, factory: Callable[[], ASRProvider]) -> None:
        """Register an ASR provider. Used by tests and future plugins."""
        self._asr[name] = factory

    def asr_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._asr))

    def asr(self, name: str) -> ASRProvider:
        factory = self._asr.get(name)
        if factory is None:
            raise ProviderUnavailableError(
                f"No speech recognition provider called {name!r} is registered.",
                reason=f"Known providers: {', '.join(sorted(self._asr)) or 'none'}.",
                context={"provider": name, "task": ModelTask.ASR.value},
            )
        return factory()


_DEFAULT_REGISTRY = ProviderRegistry()


def get_asr_provider(name: str) -> ASRProvider:
    """Construct an ASR provider by name from the default registry."""
    return _DEFAULT_REGISTRY.asr(name)


def default_registry() -> ProviderRegistry:
    return _DEFAULT_REGISTRY


__all__ = ["ProviderRegistry", "default_registry", "get_asr_provider"]
