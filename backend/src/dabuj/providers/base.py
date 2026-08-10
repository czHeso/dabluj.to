"""Shared provider vocabulary.

A provider reports what it can do *before* it is asked to do it. That lets the
application refuse impossible combinations with a clear message -- "this model
cannot produce word timestamps" -- instead of discovering the problem halfway
through a two-hour job.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dabuj.domain.quality import Device, Precision


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """What a loaded provider supports on this machine.

    Distinct from ``ModelCapabilities`` in the catalog: that describes a model
    in the abstract, this describes a provider *runtime* as installed here. A
    CUDA-capable model on a machine with no GPU has CUDA in the former and not
    in the latter.
    """

    devices: tuple[Device, ...] = (Device.CPU,)
    precisions: tuple[Precision, ...] = (Precision.AUTO,)
    languages: tuple[str, ...] = ()
    word_timestamps: bool = False
    language_detection: bool = False
    #: True when the provider would transmit data off this machine. Any
    #: provider setting this must be behind the explicit cloud opt-in.
    is_cloud: bool = False

    def supports_device(self, device: Device) -> bool:
        return device is Device.AUTO or device in self.devices

    def supports_language(self, code: str) -> bool:
        if not self.languages:
            return True
        return code.split("-", 1)[0].lower() in self.languages


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    """Identity of a provider, recorded in projects for reproducibility."""

    name: str
    version: str | None = None
    runtime: str | None = None
    runtime_version: str | None = None
    model_id: str | None = None
    model_revision: str | None = None
    device: Device = Device.CPU
    precision: Precision = Precision.AUTO
    extra: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "runtime": self.runtime,
            "runtime_version": self.runtime_version,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "device": self.device.value,
            "precision": self.precision.value,
            **self.extra,
        }


__all__ = ["ProviderCapabilities", "ProviderInfo"]
