"""Quality profiles, devices and numeric precision.

A *quality profile* is a recommendation, never a constraint. It picks sensible
defaults for a machine; every individual knob it sets can be overridden by the
user. See ``dabuj.hardware.profiles`` for the rules that choose one.
"""

from __future__ import annotations

from enum import Enum


class QualityProfile(str, Enum):
    """Preset trading quality against speed and memory.

    Ordered from cheapest to most expensive; :meth:`rank` makes the ordering
    usable in comparisons without relying on declaration order elsewhere.
    """

    LOW = "low"
    BALANCED = "balanced"
    HIGH = "high"
    ULTRA = "ultra"

    @property
    def rank(self) -> int:
        return _PROFILE_RANK[self]

    @property
    def label(self) -> str:
        return _PROFILE_LABEL[self]

    @property
    def description(self) -> str:
        return _PROFILE_DESCRIPTION[self]


_PROFILE_RANK: dict[QualityProfile, int] = {
    QualityProfile.LOW: 0,
    QualityProfile.BALANCED: 1,
    QualityProfile.HIGH: 2,
    QualityProfile.ULTRA: 3,
}

_PROFILE_LABEL: dict[QualityProfile, str] = {
    QualityProfile.LOW: "Low (CPU friendly)",
    QualityProfile.BALANCED: "Balanced",
    QualityProfile.HIGH: "High",
    QualityProfile.ULTRA: "Ultra (best available)",
}

_PROFILE_DESCRIPTION: dict[QualityProfile, str] = {
    QualityProfile.LOW: (
        "Quantised models on the CPU. Runs on an ordinary laptop with no "
        "discrete GPU. Slower, but genuinely usable."
    ),
    QualityProfile.BALANCED: (
        "Good quality at a reasonable speed and memory footprint. The right "
        "default for most modern machines."
    ),
    QualityProfile.HIGH: (
        "Larger models on a capable GPU. Noticeably better accuracy, "
        "especially on accented or noisy audio."
    ),
    QualityProfile.ULTRA: (
        "The best models available, prioritising quality over speed. Intended for workstations."
    ),
}


class Device(str, Enum):
    """Compute device for inference.

    ``AUTO`` asks the provider to pick the best device it can actually
    initialise, falling back rather than failing outright.
    """

    AUTO = "auto"
    CPU = "cpu"
    CUDA = "cuda"
    DIRECTML = "directml"
    ROCM = "rocm"
    METAL = "metal"

    @property
    def is_accelerator(self) -> bool:
        return self not in (Device.CPU, Device.AUTO)


class Precision(str, Enum):
    """Numeric precision / quantisation for inference.

    Not every provider supports every value; a provider must report what it
    supports through its capabilities rather than failing at load time.
    """

    AUTO = "auto"
    FLOAT32 = "float32"
    FLOAT16 = "float16"
    BFLOAT16 = "bfloat16"
    INT8 = "int8"
    INT8_FLOAT16 = "int8_float16"

    @property
    def is_quantised(self) -> bool:
        return self in (Precision.INT8, Precision.INT8_FLOAT16)


__all__ = ["Device", "Precision", "QualityProfile"]
