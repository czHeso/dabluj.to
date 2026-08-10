"""Local hardware detection.

Everything gathered here stays on the machine and is used for exactly two
things: recommending a quality profile, and telling the user why a model will
not fit. Nothing that identifies the machine -- serial numbers, MAC addresses,
hostnames, usernames -- is ever collected (docs/PRIVACY.md).

Detection is best-effort by design. A missing GPU reading produces ``None``,
never a guess: recommending a profile from an invented VRAM figure is worse
than recommending a conservative one.
"""

from __future__ import annotations

import contextlib
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import psutil

from dabuj.domain.quality import Device
from dabuj.logging import get_logger

logger = get_logger(__name__)

_NVIDIA_SMI_TIMEOUT = 10
_BYTES_PER_GIB = 1024**3


class GPUVendor(str, Enum):
    NVIDIA = "nvidia"
    AMD = "amd"
    INTEL = "intel"
    APPLE = "apple"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CPUInfo:
    """What we know about the processor."""

    name: str
    architecture: str
    physical_cores: int | None
    logical_cores: int | None
    max_frequency_mhz: float | None = None

    @property
    def usable_threads(self) -> int:
        """A sensible default thread count for CPU inference.

        Physical cores, not logical: hyper-threaded siblings share execution
        units, and oversubscribing them makes GEMM-heavy inference slower, not
        faster. Falls back conservatively when the count is unknown.
        """
        return self.physical_cores or self.logical_cores or 4


@dataclass(frozen=True, slots=True)
class GPUInfo:
    """One detected GPU.

    ``total_memory_bytes`` is ``None`` when it could not be read. Callers must
    treat that as "unknown", not "zero".
    """

    name: str
    vendor: GPUVendor
    total_memory_bytes: int | None = None
    free_memory_bytes: int | None = None
    driver_version: str | None = None
    compute_capability: str | None = None

    @property
    def total_memory_gib(self) -> float | None:
        if self.total_memory_bytes is None:
            return None
        return self.total_memory_bytes / _BYTES_PER_GIB

    @property
    def free_memory_gib(self) -> float | None:
        if self.free_memory_bytes is None:
            return None
        return self.free_memory_bytes / _BYTES_PER_GIB


@dataclass(frozen=True, slots=True)
class AcceleratorInfo:
    """Which inference backends are actually usable on this machine.

    A flag here means "the runtime is present and reports a usable device",
    not "this platform could theoretically support it".
    """

    cuda: bool = False
    cuda_version: str | None = None
    directml: bool = False
    rocm: bool = False
    metal: bool = False

    @property
    def available_devices(self) -> tuple[Device, ...]:
        devices = [Device.CPU]
        if self.cuda:
            devices.append(Device.CUDA)
        if self.directml:
            devices.append(Device.DIRECTML)
        if self.rocm:
            devices.append(Device.ROCM)
        if self.metal:
            devices.append(Device.METAL)
        return tuple(devices)

    @property
    def best_device(self) -> Device:
        """The fastest device we can actually use, preferring maturity.

        CUDA first because it is the best-supported path in the ML runtimes
        Dabuj uses; Metal next on Apple silicon; then the more experimental
        backends; CPU always works.
        """
        for device in (Device.CUDA, Device.METAL, Device.ROCM, Device.DIRECTML):
            if device in self.available_devices:
                return device
        return Device.CPU


@dataclass(frozen=True, slots=True)
class SystemInfo:
    """A complete picture of the local machine."""

    os_name: str
    os_version: str
    machine: str
    python_version: str
    cpu: CPUInfo
    total_memory_bytes: int
    available_memory_bytes: int
    gpus: tuple[GPUInfo, ...] = ()
    accelerators: AcceleratorInfo = field(default_factory=AcceleratorInfo)
    free_disk_bytes: int | None = None

    @property
    def total_memory_gib(self) -> float:
        return self.total_memory_bytes / _BYTES_PER_GIB

    @property
    def available_memory_gib(self) -> float:
        return self.available_memory_bytes / _BYTES_PER_GIB

    @property
    def primary_gpu(self) -> GPUInfo | None:
        """The GPU with the most memory, which is the one we would use."""
        with_memory = [g for g in self.gpus if g.total_memory_bytes is not None]
        if with_memory:
            return max(with_memory, key=lambda g: g.total_memory_bytes or 0)
        return self.gpus[0] if self.gpus else None

    @property
    def usable_vram_gib(self) -> float | None:
        """VRAM on the primary GPU, or ``None`` if there is none or it is unknown."""
        gpu = self.primary_gpu
        if gpu is None or not self.accelerators.available_devices[1:]:
            return None
        return gpu.total_memory_gib

    def to_dict(self) -> dict[str, Any]:
        """Serialisable summary for the API and for diagnostic reports."""
        return {
            "os": {"name": self.os_name, "version": self.os_version, "machine": self.machine},
            "python_version": self.python_version,
            "cpu": {
                "name": self.cpu.name,
                "architecture": self.cpu.architecture,
                "physical_cores": self.cpu.physical_cores,
                "logical_cores": self.cpu.logical_cores,
                "max_frequency_mhz": self.cpu.max_frequency_mhz,
            },
            "memory": {
                "total_gib": round(self.total_memory_gib, 2),
                "available_gib": round(self.available_memory_gib, 2),
            },
            "gpus": [
                {
                    "name": gpu.name,
                    "vendor": gpu.vendor.value,
                    "total_memory_gib": (
                        round(gpu.total_memory_gib, 2) if gpu.total_memory_gib else None
                    ),
                    "free_memory_gib": (
                        round(gpu.free_memory_gib, 2) if gpu.free_memory_gib else None
                    ),
                    "driver_version": gpu.driver_version,
                    "compute_capability": gpu.compute_capability,
                }
                for gpu in self.gpus
            ],
            "accelerators": {
                "cuda": self.accelerators.cuda,
                "cuda_version": self.accelerators.cuda_version,
                "directml": self.accelerators.directml,
                "rocm": self.accelerators.rocm,
                "metal": self.accelerators.metal,
                "best_device": self.accelerators.best_device.value,
            },
            "free_disk_gib": (
                round(self.free_disk_bytes / _BYTES_PER_GIB, 2)
                if self.free_disk_bytes is not None
                else None
            ),
        }


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _detect_cpu() -> CPUInfo:
    name = platform.processor() or platform.machine() or "Unknown CPU"

    # platform.processor() returns the bare architecture on Linux and often an
    # empty string on Apple silicon, so prefer a real model name where the OS
    # exposes one cheaply.
    if platform.system() == "Windows":
        name = os.environ.get("PROCESSOR_IDENTIFIER", name)
    elif platform.system() == "Linux":
        try:
            with Path("/proc/cpuinfo").open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if line.startswith("model name"):
                        name = line.split(":", 1)[1].strip()
                        break
        except OSError:
            # Unavailable in some containers; the fallback name is fine.
            pass
    elif platform.system() == "Darwin":
        name = _run_capture(["sysctl", "-n", "machdep.cpu.brand_string"]) or name

    frequency: float | None = None
    try:
        freq = psutil.cpu_freq()
        frequency = freq.max or freq.current if freq else None
    except (OSError, AttributeError, NotImplementedError):
        # Unavailable in many containers and on some ARM kernels.
        pass

    return CPUInfo(
        name=name.strip(),
        architecture=platform.machine(),
        physical_cores=psutil.cpu_count(logical=False),
        logical_cores=psutil.cpu_count(logical=True),
        max_frequency_mhz=frequency,
    )


def _run_capture(command: list[str], timeout: int = 10) -> str | None:
    """Run a command and return stripped stdout, or ``None`` on any failure."""
    executable = shutil.which(command[0])
    if executable is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - argv array, resolved binary
            [executable, *command[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _detect_nvidia_gpus() -> tuple[list[GPUInfo], str | None]:
    """Query ``nvidia-smi``.

    Chosen over importing torch or pynvml because it needs no heavy dependency
    and works whether or not a GPU-enabled runtime is installed. Absence of
    ``nvidia-smi`` is the normal case on most consumer machines.
    """
    output = _run_capture(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.free,driver_version,compute_cap",
            "--format=csv,noheader,nounits",
        ],
        timeout=_NVIDIA_SMI_TIMEOUT,
    )
    if not output:
        return [], None

    gpus: list[GPUInfo] = []
    driver: str | None = None
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        name, total_mib, free_mib, driver_version = parts[0], parts[1], parts[2], parts[3]
        compute_cap = parts[4] if len(parts) > 4 else None
        driver = driver_version or driver

        def _mib_to_bytes(value: str) -> int | None:
            try:
                return int(float(value)) * 1024 * 1024
            except ValueError:
                return None

        gpus.append(
            GPUInfo(
                name=name,
                vendor=GPUVendor.NVIDIA,
                total_memory_bytes=_mib_to_bytes(total_mib),
                free_memory_bytes=_mib_to_bytes(free_mib),
                driver_version=driver_version or None,
                compute_capability=compute_cap or None,
            )
        )
    return gpus, driver


def _detect_accelerators(has_nvidia: bool) -> AcceleratorInfo:
    """Determine which inference backends are genuinely usable.

    Each flag requires positive evidence. In particular CUDA is only reported
    when a CUDA-capable runtime is importable *and* an NVIDIA GPU is present --
    a driver without a runtime cannot run inference, and claiming otherwise
    produces a confusing failure later.
    """
    system = platform.system()
    cuda = False
    cuda_version: str | None = None
    directml = False
    rocm = False
    metal = False

    if has_nvidia:
        try:
            import torch  # noqa: PLC0415 - optional heavy dependency

            cuda = bool(torch.cuda.is_available())
            cuda_version = getattr(torch.version, "cuda", None)
        except Exception:  # noqa: BLE001 - torch is optional and can fail to load
            # ctranslate2 (the faster-whisper backend) can use CUDA without
            # torch, so fall back to asking it directly.
            try:
                import ctranslate2  # noqa: PLC0415

                cuda = ctranslate2.get_cuda_device_count() > 0
            except Exception:  # noqa: BLE001
                cuda = False

    if system == "Darwin" and platform.machine() == "arm64":
        # Apple silicon always has a Metal-capable GPU.
        metal = True

    if system == "Windows":
        try:
            import onnxruntime  # noqa: PLC0415

            directml = "DmlExecutionProvider" in onnxruntime.get_available_providers()
        except Exception:  # noqa: BLE001
            directml = False

    if system == "Linux":
        try:
            import torch  # noqa: PLC0415

            rocm = bool(getattr(torch.version, "hip", None)) and torch.cuda.is_available()
        except Exception:  # noqa: BLE001
            rocm = False

    return AcceleratorInfo(
        cuda=cuda, cuda_version=cuda_version, directml=directml, rocm=rocm, metal=metal
    )


def _detect_apple_gpu() -> list[GPUInfo]:
    """Report the integrated GPU on Apple silicon.

    Metal shares system memory, so VRAM is deliberately left unknown rather
    than reported as the full RAM figure, which would badly mislead the
    profile engine.
    """
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return []
    return [GPUInfo(name=f"Apple {platform.machine()} GPU", vendor=GPUVendor.APPLE)]


def detect_system(disk_path: str | os.PathLike[str] | None = None) -> SystemInfo:
    """Inspect the local machine.

    Args:
        disk_path: Where to measure free space. Defaults to the current drive.

    Returns:
        A :class:`SystemInfo`. Never raises: any probe that fails contributes
        ``None`` rather than aborting detection, because a partial picture is
        still useful and this runs on every start-up.
    """
    memory = psutil.virtual_memory()
    nvidia_gpus, _ = _detect_nvidia_gpus()
    gpus = nvidia_gpus or _detect_apple_gpu()

    free_disk: int | None = None
    with contextlib.suppress(OSError):
        free_disk = shutil.disk_usage(disk_path or Path.cwd()).free

    return SystemInfo(
        os_name=platform.system() or "Unknown",
        os_version=platform.release(),
        machine=platform.machine(),
        python_version=platform.python_version(),
        cpu=_detect_cpu(),
        total_memory_bytes=memory.total,
        available_memory_bytes=memory.available,
        gpus=tuple(gpus),
        accelerators=_detect_accelerators(has_nvidia=bool(nvidia_gpus)),
        free_disk_bytes=free_disk,
    )


__all__ = [
    "AcceleratorInfo",
    "CPUInfo",
    "GPUInfo",
    "GPUVendor",
    "SystemInfo",
    "detect_system",
]
