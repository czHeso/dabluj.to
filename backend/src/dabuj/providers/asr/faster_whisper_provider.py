"""Speech recognition via faster-whisper (CTranslate2).

Chosen as the default ASR backend because it is the only option that satisfies
every hard requirement at once: genuinely usable on CPU (int8 quantisation),
strong Czech and German alongside English, word-level timestamps, built-in
language detection and VAD, a permissive MIT license on both the runtime and
the weights, and mature Windows support. The full comparison is in
docs/adr/0003-asr-provider.md.

The runtime is imported lazily. ``pip install dabuj`` must work on a machine
that will never run recognition, and a missing optional dependency has to
produce an explanation rather than an ImportError traceback at start-up.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from dabuj.domain.language import Language, LanguageDetection
from dabuj.domain.quality import Device, Precision
from dabuj.domain.transcript import Segment, Transcript, Word
from dabuj.errors import (
    InsufficientResourcesError,
    PipelineError,
    ProviderUnavailableError,
)
from dabuj.logging import get_logger
from dabuj.models.catalog import WHISPER_LANGUAGES
from dabuj.pipeline.cancellation import CancellationToken, NullCancellationToken
from dabuj.providers.asr.base import ASROptions, ASRProgressHook, ASRResult
from dabuj.providers.base import ProviderCapabilities, ProviderInfo

logger = get_logger(__name__)

PROVIDER_NAME = "faster_whisper"

#: Dabuj precision -> CTranslate2 compute_type.
_COMPUTE_TYPES: dict[Precision, str] = {
    Precision.AUTO: "default",
    Precision.INT8: "int8",
    Precision.INT8_FLOAT16: "int8_float16",
    Precision.FLOAT16: "float16",
    Precision.FLOAT32: "float32",
    # CTranslate2 has no distinct bfloat16 compute type for Whisper; float32 is
    # the honest mapping rather than silently downgrading to float16.
    Precision.BFLOAT16: "float32",
}

_DEVICES: dict[Device, str] = {
    Device.AUTO: "auto",
    Device.CPU: "cpu",
    Device.CUDA: "cuda",
}

#: Substrings that identify an out-of-memory failure across CUDA and CPU paths.
_OOM_MARKERS = (
    "out of memory",
    "cuda_error_out_of_memory",
    "cublas_status_alloc_failed",
    "bad_alloc",
)


def _confidence_from_logprob(avg_logprob: float | None) -> float | None:
    """Convert Whisper's average log-probability into a 0-1 confidence.

    ``exp(avg_logprob)`` is the standard interpretation: the geometric mean of
    the per-token probabilities. It is a genuine model output, not an invented
    score, but it is a *relative* signal -- useful for ranking segments by how
    unsure the model was, not for claiming a calibrated accuracy.
    """
    if avg_logprob is None:
        return None
    try:
        return max(0.0, min(1.0, math.exp(avg_logprob)))
    except (OverflowError, ValueError):
        return None


class FasterWhisperProvider:
    """ASR provider backed by CTranslate2's Whisper implementation."""

    name = PROVIDER_NAME

    def __init__(self) -> None:
        self._model: Any = None
        self._device: Device = Device.CPU
        self._precision: Precision = Precision.AUTO
        self._model_path: Path | None = None
        self._runtime_version: str | None = None

    # -- capabilities -----------------------------------------------------

    @property
    def capabilities(self) -> ProviderCapabilities:
        """What this provider supports here and now.

        CUDA is advertised only when CTranslate2 reports an actual device, so
        the UI cannot offer a GPU that would fail on selection.
        """
        devices: list[Device] = [Device.CPU]
        try:
            import ctranslate2  # noqa: PLC0415

            if ctranslate2.get_cuda_device_count() > 0:
                devices.append(Device.CUDA)
        except Exception:  # noqa: BLE001 - absence is the normal case
            # No CUDA runtime, or no GPU. Both are ordinary; CPU stays available.
            logger.debug("no CUDA device reported by ctranslate2", exc_info=True)

        return ProviderCapabilities(
            devices=tuple(devices),
            precisions=(
                Precision.AUTO,
                Precision.INT8,
                Precision.INT8_FLOAT16,
                Precision.FLOAT16,
                Precision.FLOAT32,
            ),
            languages=WHISPER_LANGUAGES,
            word_timestamps=True,
            language_detection=True,
            is_cloud=False,
        )

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def provider_info(self, model_id: str | None = None) -> ProviderInfo:
        return ProviderInfo(
            name=self.name,
            version=self._package_version("faster_whisper"),
            runtime="ctranslate2",
            runtime_version=self._runtime_version,
            model_id=model_id,
            device=self._device,
            precision=self._precision,
        )

    @staticmethod
    def _package_version(package: str) -> str | None:
        from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

        try:
            return version(package)
        except PackageNotFoundError:
            return None

    # -- lifecycle --------------------------------------------------------

    def load(
        self,
        model_path: Path,
        *,
        device: Device = Device.AUTO,
        precision: Precision = Precision.AUTO,
        cpu_threads: int = 0,
    ) -> None:
        """Load a CTranslate2 Whisper model directory.

        Raises:
            ProviderUnavailableError: If faster-whisper is not installed, or the
                requested device is not usable.
            InsufficientResourcesError: If the model does not fit in memory.
        """
        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415
        except ImportError as exc:
            raise ProviderUnavailableError(
                "Speech recognition support is not installed.",
                reason="The faster-whisper package could not be imported.",
                suggestions=[
                    'Install it with: pip install "dabuj[asr]"',
                    "Then check the installation with: dabuj doctor",
                ],
            ) from exc

        try:
            import ctranslate2  # noqa: PLC0415

            self._runtime_version = getattr(ctranslate2, "__version__", None)
        except ImportError:
            self._runtime_version = None

        resolved_device = self._resolve_device(device)
        compute_type = _COMPUTE_TYPES.get(precision, "default")

        logger.info(
            "loading ASR model",
            extra={
                "provider": self.name,
                "device": resolved_device.value,
                "precision": precision.value,
            },
        )

        try:
            self._model = WhisperModel(
                str(model_path),
                device=_DEVICES[resolved_device],
                compute_type=compute_type,
                cpu_threads=cpu_threads,
            )
        except Exception as exc:  # noqa: BLE001 - runtime raises bare exceptions
            self._model = None
            raise self._translate_load_error(exc, model_path, resolved_device, precision) from exc

        self._device = resolved_device
        self._precision = precision
        self._model_path = model_path

    def _resolve_device(self, device: Device) -> Device:
        """Turn a requested device into one that will actually work.

        ``AUTO`` picks the best available. An explicit request for an
        unavailable device is an *error*, never a silent downgrade: quietly
        moving a job to the CPU could turn twenty minutes into six hours
        without the user being told why.
        """
        available = self.capabilities.devices

        if device is Device.AUTO:
            return Device.CUDA if Device.CUDA in available else Device.CPU

        if device not in _DEVICES:
            raise ProviderUnavailableError(
                f"faster-whisper cannot use the {device.value} backend.",
                reason="This provider supports CPU and CUDA only.",
                suggestions=[
                    "Use --device cpu, or --device cuda on an NVIDIA GPU",
                    "See docs/MODELS.md for which backends each provider supports",
                ],
                context={"device": device.value},
            )

        if device not in available:
            raise ProviderUnavailableError(
                f"The {device.value} backend is not available on this machine.",
                reason=(
                    "No CUDA device was detected. This usually means there is no "
                    "NVIDIA GPU, or the CUDA runtime libraries are not installed."
                ),
                suggestions=[
                    "Run on the CPU instead: --device cpu",
                    "Check what was detected with: dabuj system-info",
                    "CUDA 12 and cuDNN 9 are required for GPU inference",
                ],
                context={"device": device.value},
            )
        return device

    def _translate_load_error(
        self, exc: Exception, model_path: Path, device: Device, precision: Precision
    ) -> Exception:
        """Turn a runtime exception into something a user can act on."""
        message = str(exc).lower()

        if any(marker in message for marker in _OOM_MARKERS):
            return InsufficientResourcesError(
                "There was not enough memory to load the speech recognition model.",
                reason=str(exc),
                suggestions=[
                    "Choose a smaller model, for example whisper-small",
                    "Use int8 quantisation: --precision int8",
                    "Switch to the CPU: --device cpu",
                    "Close other applications and try again",
                ],
                context={"device": device.value, "precision": precision.value},
            )

        if "no such file" in message or "does not exist" in message or "unable to open" in message:
            return ProviderUnavailableError(
                "The speech recognition model files could not be read.",
                reason=str(exc),
                suggestions=[
                    "Reinstall the model: dabuj models install <model-id>",
                    "Check that the models directory is readable",
                ],
                context={"model_path": str(model_path)},
            )

        return ProviderUnavailableError(
            "The speech recognition model could not be loaded.",
            reason=str(exc),
            suggestions=[
                "Try --device cpu --precision int8",
                "Run dabuj doctor to check the installation",
            ],
            context={"device": device.value, "precision": precision.value},
        )

    def unload(self) -> None:
        """Release the model. Safe to call when nothing is loaded."""
        self._model = None
        self._model_path = None

    def _require_model(self) -> Any:
        if self._model is None:
            raise PipelineError(
                "No speech recognition model is loaded.",
                reason="This is an internal error: load() must be called before transcribing.",
            )
        return self._model

    # -- recognition ------------------------------------------------------

    def detect_language(self, audio_path: Path) -> LanguageDetection:
        """Identify the spoken language.

        ``transcribe()`` performs detection eagerly and returns the result
        alongside a *lazy* segment generator, so reading ``info`` without
        iterating costs only the detection pass, not a full transcription.
        """
        model = self._require_model()
        try:
            _, info = model.transcribe(str(audio_path), vad_filter=True)
        except Exception as exc:  # noqa: BLE001
            raise PipelineError(
                "The language of the audio could not be detected.",
                reason=str(exc),
                suggestions=["Specify the language explicitly, e.g. --language cs"],
            ) from exc

        return LanguageDetection(
            language=Language.parse(info.language),
            confidence=float(info.language_probability or 0.0),
        )

    def transcribe(
        self,
        audio_path: Path,
        *,
        options: ASROptions | None = None,
        on_progress: ASRProgressHook | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ASRResult:
        """Transcribe an audio file into a :class:`Transcript`."""
        model = self._require_model()
        options = options or ASROptions()
        token = cancellation or NullCancellationToken()
        token.raise_if_cancelled("Transcription")

        language_code: str | None = None
        if options.language is not None and not options.language.is_auto:
            language_code = options.language.base_code
            if language_code not in WHISPER_LANGUAGES:
                raise PipelineError(
                    f"Whisper does not support the language {options.language.code!r}.",
                    reason="The selected model was not trained on this language.",
                    suggestions=[
                        "Use --language auto to detect it",
                        "See docs/MODELS.md for the supported language list",
                    ],
                    context={"language": options.language.code},
                )

        started = time.monotonic()
        try:
            raw_segments, info = model.transcribe(
                str(audio_path),
                language=language_code,
                task="translate" if options.translate_to_english else "transcribe",
                beam_size=options.beam_size,
                temperature=options.temperature,
                word_timestamps=options.word_timestamps,
                vad_filter=options.vad_filter,
                initial_prompt=options.initial_prompt,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._translate_runtime_error(exc) from exc

        total_duration = float(getattr(info, "duration", 0.0) or 0.0)
        detection = LanguageDetection(
            language=Language.parse(info.language),
            confidence=float(info.language_probability or 0.0),
        )

        segments: list[Segment] = []
        warnings: list[str] = []

        try:
            # The generator is where the actual work happens: each `next()`
            # decodes the following window. Cancellation is therefore checked
            # here, between windows, which is the only safe point.
            for raw in raw_segments:
                token.raise_if_cancelled("Transcription")
                segments.append(self._to_segment(raw))

                if on_progress and total_duration > 0:
                    on_progress(
                        min(1.0, float(raw.end) / total_duration),
                        f"Segment {len(segments)}",
                    )
        except Exception as exc:
            if type(exc).__name__ == "CancelledError" or isinstance(exc, PipelineError):
                raise
            raise self._translate_runtime_error(exc) from exc

        elapsed = time.monotonic() - started
        realtime_factor = (total_duration / elapsed) if elapsed > 0 and total_duration else None

        if not detection.is_confident and language_code is None:
            warnings.append(
                f"The language was detected as {detection.language.name} with only "
                f"{detection.confidence:.0%} confidence. If that is wrong, set the "
                "language explicitly and transcribe again."
            )
        if not segments:
            warnings.append(
                "No speech was found in this audio. It may be silent, music-only, "
                "or the voice activity filter may have removed everything."
            )

        if on_progress:
            on_progress(1.0, None)

        transcript = Transcript(
            segments=segments,
            language=detection.language.code,
            language_confidence=detection.confidence,
            duration=total_duration or None,
        )

        return ASRResult(
            transcript=transcript,
            detection=detection,
            provider=self.provider_info(),
            realtime_factor=realtime_factor,
            warnings=warnings,
        )

    @staticmethod
    def _to_segment(raw: Any) -> Segment:
        """Convert one faster-whisper segment into a domain :class:`Segment`."""
        words: list[Word] = []
        for raw_word in getattr(raw, "words", None) or []:
            text = str(raw_word.word)
            if not text.strip():
                continue
            words.append(
                Word(
                    text=text,
                    start=max(0.0, float(raw_word.start)),
                    end=max(0.0, float(raw_word.end)),
                    confidence=(
                        float(raw_word.probability)
                        if getattr(raw_word, "probability", None) is not None
                        else None
                    ),
                )
            )

        metadata: dict[str, Any] = {}
        no_speech = getattr(raw, "no_speech_prob", None)
        if no_speech is not None:
            metadata["no_speech_prob"] = round(float(no_speech), 4)
        compression = getattr(raw, "compression_ratio", None)
        if compression is not None:
            metadata["compression_ratio"] = round(float(compression), 4)

        return Segment(
            start=max(0.0, float(raw.start)),
            end=max(0.0, float(raw.end)),
            raw_text=str(raw.text),
            confidence=_confidence_from_logprob(getattr(raw, "avg_logprob", None)),
            words=words,
            metadata=metadata,
        )

    def _translate_runtime_error(self, exc: Exception) -> Exception:
        message = str(exc).lower()
        if any(marker in message for marker in _OOM_MARKERS):
            return InsufficientResourcesError(
                "Transcription ran out of memory.",
                reason=str(exc),
                suggestions=[
                    "Use a smaller model or int8 quantisation",
                    "Switch to the CPU: --device cpu",
                ],
                context={"device": self._device.value},
            )
        return PipelineError(
            "Transcription failed.",
            reason=str(exc),
            suggestions=[
                "Check the audio file is valid: dabuj probe <file>",
                "Run with --debug for the full technical detail in the log",
            ],
        )


__all__ = ["PROVIDER_NAME", "FasterWhisperProvider"]
