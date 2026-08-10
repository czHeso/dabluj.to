"""The speech-recognition provider contract."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from dabuj.domain.language import Language, LanguageDetection
from dabuj.domain.quality import Device, Precision
from dabuj.domain.transcript import Transcript
from dabuj.pipeline.cancellation import CancellationToken
from dabuj.providers.base import ProviderCapabilities, ProviderInfo


@dataclass(frozen=True, slots=True)
class ASROptions:
    """Per-run recognition settings.

    Everything here is safe to expose to advanced users. Values that would let
    a user construct an invalid request -- an unsupported device, a language
    the model does not know -- are validated by the provider at load time.
    """

    language: Language | None = None
    #: Emit per-word timings. Costs roughly 10-20% more time.
    word_timestamps: bool = True
    #: Trim silence with voice activity detection before recognising. Also
    #: suppresses much of Whisper's hallucination on non-speech audio.
    vad_filter: bool = True
    #: Beam width. 1 is greedy and fastest; 5 is the Whisper default.
    beam_size: int = 5
    #: Sampling temperature. 0.0 is deterministic, which is what we want for
    #: reproducibility; the provider may still escalate on decoding failure.
    temperature: float = 0.0
    #: Optional prompt biasing the first window towards a domain vocabulary.
    initial_prompt: str | None = None
    #: Translate to English instead of transcribing verbatim. A Whisper
    #: capability, distinct from Dabuj's own translation stage.
    translate_to_english: bool = False
    #: Number of CPU threads. 0 lets the runtime decide.
    cpu_threads: int = 0

    def cache_fingerprint(self) -> str:
        """Stable representation of the options, for cache keys."""
        return (
            f"lang={self.language.code if self.language else 'auto'};"
            f"words={self.word_timestamps};vad={self.vad_filter};"
            f"beam={self.beam_size};temp={self.temperature};"
            f"prompt={self.initial_prompt or ''};en={self.translate_to_english}"
        )


@dataclass(slots=True)
class ASRResult:
    """What a recognition run produced."""

    transcript: Transcript
    detection: LanguageDetection | None = None
    provider: ProviderInfo | None = None
    #: Audio seconds processed per wall-clock second.
    realtime_factor: float | None = None
    warnings: list[str] = field(default_factory=list)


#: Called with a fraction in ``[0, 1]`` and an optional message.
ASRProgressHook = Callable[[float, str | None], None]


@runtime_checkable
class ASRProvider(Protocol):
    """A local (or, explicitly, remote) speech recognition backend.

    Implementations must:

    * import their runtime lazily, so an uninstalled optional dependency
      produces a clear message rather than an ImportError at start-up;
    * honour cancellation between segments;
    * report progress against audio duration, not segment count, since
      segments vary wildly in length;
    * never silently substitute a different model or device -- a fallback is a
      decision the user makes.
    """

    name: str

    @property
    def capabilities(self) -> ProviderCapabilities:
        """What this provider supports, as loaded on this machine."""
        ...

    def load(
        self,
        model_path: Path,
        *,
        device: Device = Device.AUTO,
        precision: Precision = Precision.AUTO,
        cpu_threads: int = 0,
    ) -> None:
        """Load the model into memory.

        Raises:
            ProviderUnavailableError: If the runtime is missing or unusable.
            InsufficientResourcesError: If the model does not fit.
        """
        ...

    def unload(self) -> None:
        """Release the model and any device memory it holds."""
        ...

    def detect_language(self, audio_path: Path) -> LanguageDetection:
        """Identify the spoken language from the beginning of the audio."""
        ...

    def transcribe(
        self,
        audio_path: Path,
        *,
        options: ASROptions | None = None,
        on_progress: ASRProgressHook | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ASRResult:
        """Transcribe an audio file.

        Args:
            audio_path: A file the provider can read. The pipeline supplies
                16 kHz mono WAV.
            options: Recognition settings.
            on_progress: Progress hook.
            cancellation: Checked between segments.

        Raises:
            CancelledError: If cancelled.
            PipelineError: If recognition fails.
        """
        ...


__all__ = ["ASROptions", "ASRProgressHook", "ASRProvider", "ASRResult"]
