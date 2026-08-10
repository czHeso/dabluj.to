"""Pluggable model providers.

Each task (recognition, diarization, translation, speech synthesis, source
separation) is defined by a protocol. Concrete providers encapsulate one
runtime each -- CTranslate2, ONNX Runtime, PyTorch, llama.cpp -- and the rest
of the application never learns which one is in use.

The rule that keeps this honest: a provider owns its runtime's quirks
entirely. There is no ``if provider_name == "..."`` anywhere outside this
package.
"""

from dabuj.providers.asr.base import ASROptions, ASRProvider, ASRResult
from dabuj.providers.base import ProviderCapabilities, ProviderInfo
from dabuj.providers.registry import ProviderRegistry, get_asr_provider

__all__ = [
    "ASROptions",
    "ASRProvider",
    "ASRResult",
    "ProviderCapabilities",
    "ProviderInfo",
    "ProviderRegistry",
    "get_asr_provider",
]
