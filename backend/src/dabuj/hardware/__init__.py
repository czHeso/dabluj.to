"""Hardware detection and quality-profile recommendation."""

from dabuj.hardware.detect import (
    AcceleratorInfo,
    CPUInfo,
    GPUInfo,
    SystemInfo,
    detect_system,
)
from dabuj.hardware.profiles import ProfileRecommendation, recommend_profile

__all__ = [
    "AcceleratorInfo",
    "CPUInfo",
    "GPUInfo",
    "ProfileRecommendation",
    "SystemInfo",
    "detect_system",
    "recommend_profile",
]
