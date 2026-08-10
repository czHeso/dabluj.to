"""The catalog of models Dabuj knows how to install.

Every entry was checked against its primary source -- the upstream repository,
the model card and the license file -- and the findings are recorded in
docs/MODELS.md with links. Nothing here is inferred from a blog post.

Two rules govern what may appear in this catalog:

1. **Licenses are stated, never assumed.** ``commercial_use`` is only ``True``
   where the license text plainly permits it.
2. **Sizes are approximate and labelled as such.** The authoritative size comes
   from the download source at install time, and that is what the user is
   shown before confirming.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from dabuj.domain.quality import Device, Precision, QualityProfile

#: The 99 languages the Whisper family was trained on, as ISO 639-1 codes.
#: Source: OpenAI Whisper's tokenizer LANGUAGES table (MIT).
WHISPER_LANGUAGES: tuple[str, ...] = (
    "en",
    "zh",
    "de",
    "es",
    "ru",
    "ko",
    "fr",
    "ja",
    "pt",
    "tr",
    "pl",
    "ca",
    "nl",
    "ar",
    "sv",
    "it",
    "id",
    "hi",
    "fi",
    "vi",
    "he",
    "uk",
    "el",
    "ms",
    "cs",
    "ro",
    "da",
    "hu",
    "ta",
    "no",
    "th",
    "ur",
    "hr",
    "bg",
    "lt",
    "la",
    "mi",
    "ml",
    "cy",
    "sk",
    "te",
    "fa",
    "lv",
    "bn",
    "sr",
    "az",
    "sl",
    "kn",
    "et",
    "mk",
    "br",
    "eu",
    "is",
    "hy",
    "ne",
    "mn",
    "bs",
    "kk",
    "sq",
    "sw",
    "gl",
    "mr",
    "pa",
    "si",
    "km",
    "sn",
    "yo",
    "so",
    "af",
    "oc",
    "ka",
    "be",
    "tg",
    "sd",
    "gu",
    "am",
    "yi",
    "lo",
    "uz",
    "fo",
    "ht",
    "ps",
    "tk",
    "nn",
    "mt",
    "sa",
    "lb",
    "my",
    "bo",
    "tl",
    "mg",
    "as",
    "tt",
    "haw",
    "ln",
    "ha",
    "ba",
    "jw",
    "su",
)

_MIB = 1024**2
_GIB = 1024**3


class ModelTask(str, Enum):
    """What a model does. Providers are selected by task."""

    ASR = "asr"
    DIARIZATION = "diarization"
    TRANSLATION = "translation"
    TTS = "tts"
    SEPARATION = "separation"


class ModelCapabilities(BaseModel):
    """What a model can actually do.

    The frontend derives its options from these flags, so a model that cannot
    produce word timestamps simply does not offer the checkbox. Claiming a
    capability a model lacks is worse than not having it.
    """

    model_config = ConfigDict(frozen=True)

    word_timestamps: bool = False
    language_detection: bool = False
    multilingual: bool = False
    translation_to_english: bool = False
    voice_cloning: bool = False
    speaking_rate_control: bool = False
    streaming: bool = False


class ModelSpec(BaseModel):
    """Everything needed to describe, install and select a model."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    task: ModelTask
    #: Which provider implementation can load this model.
    provider: str
    description: str = ""

    # -- download ---------------------------------------------------------
    #: Hugging Face repository, e.g. ``"Systran/faster-whisper-small"``.
    repo_id: str
    #: Pinned revision. ``"main"`` is used where upstream does not tag; the
    #: resolved commit is recorded in the project for reproducibility.
    revision: str = "main"
    #: Only these files are fetched. Keeps a multi-gigabyte repository from
    #: being pulled down when a few hundred megabytes are needed.
    allow_patterns: tuple[str, ...] = ()
    #: Approximate download size for display *before* installation. The exact
    #: figure comes from the source and is what the user confirms.
    approx_size_bytes: int = Field(default=0, ge=0)

    # -- runtime ----------------------------------------------------------
    runtime: str = ""
    supported_devices: tuple[Device, ...] = (Device.CPU,)
    precisions: tuple[Precision, ...] = (Precision.AUTO,)
    languages: tuple[str, ...] = ()
    capabilities: ModelCapabilities = ModelCapabilities()

    # -- resource requirements (approximate, for warnings not enforcement) --
    minimum_ram_bytes: int = Field(default=0, ge=0)
    recommended_ram_bytes: int = Field(default=0, ge=0)
    minimum_vram_bytes: int = Field(default=0, ge=0)
    recommended_vram_bytes: int = Field(default=0, ge=0)

    # -- provenance and licensing ----------------------------------------
    license: str = "unknown"
    license_url: str | None = None
    #: Only ``True`` where the license text plainly permits commercial use.
    commercial_use: bool = False
    #: Whether Dabuj may redistribute the weights. Currently always ``False``:
    #: Dabuj distributes no weights at all.
    redistribution: bool = False
    homepage: str | None = None
    model_card: str | None = None
    #: Set when upstream requires accepting terms before download.
    requires_license_acceptance: bool = False
    notes: str = ""

    #: Profiles this model is the default for.
    default_for_profiles: tuple[QualityProfile, ...] = ()

    @property
    def approx_size_label(self) -> str:
        if self.approx_size_bytes <= 0:
            return "unknown size"
        if self.approx_size_bytes >= _GIB:
            return f"~{self.approx_size_bytes / _GIB:.1f} GB"
        return f"~{self.approx_size_bytes / _MIB:.0f} MB"

    def supports_language(self, code: str) -> bool:
        """Whether this model handles ``code``. Empty list means unrestricted."""
        if not self.languages:
            return True
        return code.split("-", 1)[0].lower() in self.languages

    def supports_device(self, device: Device) -> bool:
        return device is Device.AUTO or device in self.supported_devices


# ---------------------------------------------------------------------------
# Built-in catalog
# ---------------------------------------------------------------------------
#
# The Whisper family, converted to CTranslate2 by SYSTRAN and served by the
# faster-whisper runtime.
#
# Licensing, verified 2026-08-10:
#   * faster-whisper (the runtime)  -- MIT   https://github.com/SYSTRAN/faster-whisper
#   * OpenAI Whisper weights        -- MIT   https://github.com/openai/whisper
#   * The SYSTRAN CTranslate2 conversions are redistributions of the MIT
#     weights and carry the same MIT terms.
# Commercial use is therefore permitted for every ASR entry below.
#
# Sizes are the CTranslate2 float16 conversions and are approximate.

_WHISPER_COMMON = {
    "task": ModelTask.ASR,
    "provider": "faster_whisper",
    "runtime": "ctranslate2",
    "supported_devices": (Device.CPU, Device.CUDA),
    "precisions": (
        Precision.AUTO,
        Precision.INT8,
        Precision.INT8_FLOAT16,
        Precision.FLOAT16,
        Precision.FLOAT32,
    ),
    "languages": WHISPER_LANGUAGES,
    "license": "MIT",
    "license_url": "https://github.com/openai/whisper/blob/main/LICENSE",
    "commercial_use": True,
    "redistribution": False,
    "homepage": "https://github.com/SYSTRAN/faster-whisper",
    "allow_patterns": ("*.json", "*.txt", "*.bin", "*.model"),
    "capabilities": ModelCapabilities(
        word_timestamps=True,
        language_detection=True,
        multilingual=True,
        translation_to_english=True,
    ),
}

BUILTIN_CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="whisper-tiny",
        name="Whisper Tiny",
        description=(
            "The smallest Whisper model. Fast enough for real-time work on any "
            "machine, but noticeably less accurate -- best for quick drafts and "
            "for checking that a pipeline works."
        ),
        repo_id="Systran/faster-whisper-tiny",
        approx_size_bytes=75 * _MIB,
        minimum_ram_bytes=1 * _GIB,
        recommended_ram_bytes=2 * _GIB,
        minimum_vram_bytes=1 * _GIB,
        model_card="https://huggingface.co/Systran/faster-whisper-tiny",
        **_WHISPER_COMMON,  # type: ignore[arg-type]
    ),
    ModelSpec(
        id="whisper-base",
        name="Whisper Base",
        description=(
            "A small step up from Tiny. Still comfortable on a low-powered "
            "laptop and a reasonable floor for clean, clearly-spoken audio."
        ),
        repo_id="Systran/faster-whisper-base",
        approx_size_bytes=145 * _MIB,
        minimum_ram_bytes=2 * _GIB,
        recommended_ram_bytes=4 * _GIB,
        minimum_vram_bytes=1 * _GIB,
        model_card="https://huggingface.co/Systran/faster-whisper-base",
        default_for_profiles=(QualityProfile.LOW,),
        **_WHISPER_COMMON,  # type: ignore[arg-type]
    ),
    ModelSpec(
        id="whisper-small",
        name="Whisper Small",
        description=(
            "The best accuracy-per-megabyte trade-off in the family, and a good "
            "default for CPU-only machines when quantised to int8."
        ),
        repo_id="Systran/faster-whisper-small",
        approx_size_bytes=484 * _MIB,
        minimum_ram_bytes=2 * _GIB,
        recommended_ram_bytes=8 * _GIB,
        minimum_vram_bytes=2 * _GIB,
        model_card="https://huggingface.co/Systran/faster-whisper-small",
        default_for_profiles=(QualityProfile.BALANCED,),
        **_WHISPER_COMMON,  # type: ignore[arg-type]
    ),
    ModelSpec(
        id="whisper-medium",
        name="Whisper Medium",
        description=(
            "Materially better on accented speech and noisy recordings. Wants a "
            "GPU, or a lot of patience on CPU."
        ),
        repo_id="Systran/faster-whisper-medium",
        approx_size_bytes=1_500 * _MIB,
        minimum_ram_bytes=6 * _GIB,
        recommended_ram_bytes=16 * _GIB,
        minimum_vram_bytes=5 * _GIB,
        recommended_vram_bytes=8 * _GIB,
        model_card="https://huggingface.co/Systran/faster-whisper-medium",
        **_WHISPER_COMMON,  # type: ignore[arg-type]
    ),
    ModelSpec(
        id="whisper-large-v3",
        name="Whisper Large v3",
        description=(
            "The most accurate Whisper release, and the strongest option for "
            "Czech and German. Needs a capable GPU to be practical."
        ),
        repo_id="Systran/faster-whisper-large-v3",
        approx_size_bytes=3_100 * _MIB,
        minimum_ram_bytes=8 * _GIB,
        recommended_ram_bytes=32 * _GIB,
        minimum_vram_bytes=8 * _GIB,
        recommended_vram_bytes=12 * _GIB,
        model_card="https://huggingface.co/Systran/faster-whisper-large-v3",
        default_for_profiles=(QualityProfile.HIGH, QualityProfile.ULTRA),
        **_WHISPER_COMMON,  # type: ignore[arg-type]
    ),
)


def find_model(model_id: str, catalog: tuple[ModelSpec, ...] = BUILTIN_CATALOG) -> ModelSpec | None:
    """Look up a model by ID."""
    return next((spec for spec in catalog if spec.id == model_id), None)


def models_for_task(
    task: ModelTask, catalog: tuple[ModelSpec, ...] = BUILTIN_CATALOG
) -> tuple[ModelSpec, ...]:
    """Every catalog entry for a given task."""
    return tuple(spec for spec in catalog if spec.task is task)


def default_model_for(
    task: ModelTask,
    profile: QualityProfile,
    catalog: tuple[ModelSpec, ...] = BUILTIN_CATALOG,
) -> ModelSpec | None:
    """The model Dabuj suggests for a task at a given quality profile.

    Falls back to the largest model at or below the requested profile, so a
    catalog without an exact match still yields a sensible answer.
    """
    candidates = models_for_task(task, catalog)
    exact = [spec for spec in candidates if profile in spec.default_for_profiles]
    if exact:
        return exact[0]

    # Nothing is marked for this profile: choose by size, capped by profile rank.
    ranked = sorted(candidates, key=lambda spec: spec.approx_size_bytes)
    if not ranked:
        return None
    index = min(profile.rank, len(ranked) - 1)
    return ranked[index]


__all__ = [
    "BUILTIN_CATALOG",
    "WHISPER_LANGUAGES",
    "ModelCapabilities",
    "ModelSpec",
    "ModelTask",
    "default_model_for",
    "find_model",
    "models_for_task",
]
