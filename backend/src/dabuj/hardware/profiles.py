"""Quality-profile recommendation.

The rules live in one ordered table rather than being scattered as magic
numbers. That makes the engine deterministic, unit-testable, and — crucially —
*explainable*: every recommendation comes with the reasons that produced it, so
the UI can say why it suggested Balanced rather than leaving the user guessing.

A profile is never chosen from VRAM alone. A machine with a big GPU but 8 GB of
system RAM will still thrash, and a fast CPU with plenty of RAM is a perfectly
good Balanced target with no GPU at all.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from dabuj.domain.quality import Device, Precision, QualityProfile
from dabuj.hardware.detect import SystemInfo

# Thresholds are expressed once, here, and referenced by the rules below.
_MIN_RAM_BALANCED_GIB = 8.0
_MIN_RAM_HIGH_GIB = 16.0
_MIN_RAM_ULTRA_GIB = 24.0

_MIN_VRAM_HIGH_GIB = 7.0
_MIN_VRAM_ULTRA_GIB = 11.0

_MIN_CORES_BALANCED = 4
_MIN_CORES_HIGH_CPU_ONLY = 8


@dataclass(frozen=True, slots=True)
class ProfileRecommendation:
    """A recommended profile plus the reasoning behind it."""

    profile: QualityProfile
    device: Device
    precision: Precision
    #: Human-readable statements supporting the choice.
    reasons: tuple[str, ...] = ()
    #: Things the user should know, e.g. "no GPU detected, this will be slow".
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile.value,
            "label": self.profile.label,
            "description": self.profile.description,
            "device": self.device.value,
            "precision": self.precision.value,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class _Rule:
    """One candidate profile and the condition that qualifies a machine for it.

    Rules are evaluated from most to least demanding; the first match wins.
    """

    profile: QualityProfile
    predicate: Callable[[SystemInfo], bool]
    reason: Callable[[SystemInfo], str]
    requires_gpu: bool = False


def _ram(system: SystemInfo) -> float:
    return system.total_memory_gib


def _vram(system: SystemInfo) -> float:
    """Usable VRAM, or 0.0 when there is no usable accelerator.

    Returning 0.0 rather than ``None`` keeps the rule predicates readable; the
    "unknown VRAM" case is handled separately when building warnings.
    """
    return system.usable_vram_gib or 0.0


def _cores(system: SystemInfo) -> int:
    return system.cpu.physical_cores or system.cpu.logical_cores or 1


_RULES: tuple[_Rule, ...] = (
    _Rule(
        profile=QualityProfile.ULTRA,
        predicate=lambda s: _vram(s) >= _MIN_VRAM_ULTRA_GIB and _ram(s) >= _MIN_RAM_ULTRA_GIB,
        reason=lambda s: (
            f"{_vram(s):.0f} GB of VRAM and {_ram(s):.0f} GB of system memory "
            "comfortably fit the largest models."
        ),
        requires_gpu=True,
    ),
    _Rule(
        profile=QualityProfile.HIGH,
        predicate=lambda s: _vram(s) >= _MIN_VRAM_HIGH_GIB and _ram(s) >= _MIN_RAM_HIGH_GIB,
        reason=lambda s: f"{_vram(s):.0f} GB of VRAM is enough for a large model on the GPU.",
        requires_gpu=True,
    ),
    _Rule(
        profile=QualityProfile.BALANCED,
        predicate=lambda s: _vram(s) > 0 and _ram(s) >= _MIN_RAM_BALANCED_GIB,
        reason=lambda s: (
            f"A usable GPU with {_vram(s):.0f} GB of VRAM handles medium models well."
            if _vram(s) > 0
            else "A usable GPU was detected."
        ),
        requires_gpu=True,
    ),
    # CPU-only machines. A genuinely strong CPU still earns Balanced.
    _Rule(
        profile=QualityProfile.BALANCED,
        predicate=lambda s: _cores(s) >= _MIN_CORES_HIGH_CPU_ONLY and _ram(s) >= _MIN_RAM_HIGH_GIB,
        reason=lambda s: (
            f"{_cores(s)} CPU cores and {_ram(s):.0f} GB of memory can run a "
            "medium quantised model at a reasonable speed."
        ),
    ),
    _Rule(
        profile=QualityProfile.LOW,
        predicate=lambda s: _cores(s) >= _MIN_CORES_BALANCED and _ram(s) >= _MIN_RAM_BALANCED_GIB,
        reason=lambda s: (
            f"{_cores(s)} CPU cores and {_ram(s):.0f} GB of memory suit a small quantised model."
        ),
    ),
)

#: Fallback when nothing matches -- a very small or very old machine.
_FLOOR = _Rule(
    profile=QualityProfile.LOW,
    predicate=lambda _: True,
    reason=lambda s: (
        f"Limited resources detected ({_cores(s)} cores, {_ram(s):.0f} GB RAM); "
        "the smallest quantised model is the safe choice."
    ),
)


def _choose_precision(profile: QualityProfile, device: Device) -> Precision:
    """Pick a numeric precision to match the profile and device.

    int8 on CPU is the single most effective lever available: it roughly halves
    memory and materially speeds up inference, at a quality cost that is small
    for speech recognition. On GPU, float16 is the sensible default, with int8
    reserved for the Low profile where the user has asked to economise.
    """
    if device is Device.CPU:
        return Precision.INT8 if profile.rank <= QualityProfile.BALANCED.rank else Precision.FLOAT32
    if profile is QualityProfile.LOW:
        return Precision.INT8_FLOAT16
    return Precision.FLOAT16


def recommend_profile(system: SystemInfo) -> ProfileRecommendation:
    """Recommend a quality profile for this machine.

    Deterministic and side-effect free: the same :class:`SystemInfo` always
    produces the same recommendation, which is what makes it testable.
    """
    device = system.accelerators.best_device
    has_accelerator = device is not Device.CPU

    reasons: list[str] = []
    warnings: list[str] = []

    matched = next(
        (
            rule
            for rule in _RULES
            if (not rule.requires_gpu or has_accelerator) and rule.predicate(system)
        ),
        _FLOOR,
    )

    reasons.append(matched.reason(system))

    if has_accelerator:
        gpu = system.primary_gpu
        if gpu is not None:
            reasons.append(f"Detected {gpu.name} using the {device.value} backend.")
        if system.usable_vram_gib is None:
            warnings.append(
                "The amount of GPU memory could not be determined, so the "
                "recommendation is based on CPU and system memory alone."
            )
    else:
        warnings.append(
            "No usable GPU was detected. Dabuj will run on the CPU, which works "
            "but is considerably slower than real time."
        )

    if _ram(system) < _MIN_RAM_BALANCED_GIB:
        warnings.append(
            f"Only {_ram(system):.0f} GB of system memory is available. Close "
            "other applications before processing long recordings."
        )

    if system.free_disk_bytes is not None:
        free_gib = system.free_disk_bytes / 1024**3
        if free_gib < 5.0:
            warnings.append(
                f"Only {free_gib:.1f} GB of disk space is free. Models and "
                "extracted audio need room."
            )

    return ProfileRecommendation(
        profile=matched.profile,
        device=device,
        precision=_choose_precision(matched.profile, device),
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


__all__ = ["ProfileRecommendation", "recommend_profile"]
