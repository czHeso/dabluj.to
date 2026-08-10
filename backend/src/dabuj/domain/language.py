"""Languages.

Deliberately *not* a closed enum. Which languages Dabuj supports is a property
of the selected model, not of the application, so the domain models a language
as a validated BCP-47-ish code plus a display name, and the authoritative list
comes from provider capabilities at runtime (see ``dabuj.providers.base``).

The table below exists only to give friendly names to codes we can name. An
unknown code is still a perfectly valid :class:`Language`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dabuj.errors import ValidationError

# Lowercase ISO 639-1/639-3 with an optional script/region suffix.
_CODE_PATTERN = re.compile(r"^[a-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$")

AUTO_DETECT = "auto"
"""Sentinel meaning "ask the model to detect the language"."""

# Display names for the codes Whisper-family models emit. Extending this table
# never grants support for a language -- it only improves the label.
_DISPLAY_NAMES: dict[str, str] = {
    "en": "English",
    "de": "German",
    "cs": "Czech",
    "sk": "Slovak",
    "pl": "Polish",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "uk": "Ukrainian",
    "ru": "Russian",
    "da": "Danish",
    "sv": "Swedish",
    "no": "Norwegian",
    "fi": "Finnish",
    "hu": "Hungarian",
    "ro": "Romanian",
    "bg": "Bulgarian",
    "el": "Greek",
    "tr": "Turkish",
    "ar": "Arabic",
    "he": "Hebrew",
    "hi": "Hindi",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "th": "Thai",
    "hr": "Croatian",
    "sr": "Serbian",
    "sl": "Slovenian",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "et": "Estonian",
    "ca": "Catalan",
    "gl": "Galician",
    "is": "Icelandic",
    "ga": "Irish",
    "cy": "Welsh",
    "fa": "Persian",
    "ur": "Urdu",
    "bn": "Bengali",
    "ta": "Tamil",
    "ms": "Malay",
    "sw": "Swahili",
}


@dataclass(frozen=True, slots=True)
class Language:
    """A language code together with a display name.

    Instances are normalised and immutable, so they are safe to use as dict
    keys and to compare directly.
    """

    code: str
    name: str

    @staticmethod
    def parse(value: str) -> Language:
        """Normalise and validate a user-supplied language code.

        Accepts ``"cs"``, ``"CS"``, ``"cs_CZ"`` and ``"cs-CZ"`` alike, as well
        as the display names in the table above, so ``--target Czech`` works.

        Raises:
            ValidationError: If the value is empty or not a plausible code.
        """
        raw = value.strip()
        if not raw:
            raise ValidationError(
                "No language was given.",
                suggestions=["Use a code such as 'en', 'de' or 'cs', or 'auto' to detect it"],
            )

        if raw.lower() == AUTO_DETECT:
            return Language(code=AUTO_DETECT, name="Auto detect")

        # Allow "Czech" as well as "cs".
        lowered = raw.lower()
        for code, name in _DISPLAY_NAMES.items():
            if name.lower() == lowered:
                return Language(code=code, name=name)

        normalised = lowered.replace("_", "-")
        if not _CODE_PATTERN.match(normalised):
            raise ValidationError(
                f"{value!r} is not a valid language code.",
                reason="Expected a two- or three-letter code, optionally with a region.",
                suggestions=[
                    "Examples: en, de, cs, pt-BR",
                    "Use 'auto' to let the model detect the language",
                ],
            )

        base = normalised.split("-", 1)[0]
        return Language(code=normalised, name=_DISPLAY_NAMES.get(base, normalised))

    @property
    def is_auto(self) -> bool:
        return self.code == AUTO_DETECT

    @property
    def base_code(self) -> str:
        """The bare language subtag, e.g. ``pt`` for ``pt-BR``."""
        return self.code.split("-", 1)[0]

    def __str__(self) -> str:
        return self.code


def display_name(code: str) -> str:
    """Best-effort human name for a raw code, falling back to the code itself."""
    return _DISPLAY_NAMES.get(code.split("-", 1)[0].lower(), code)


@dataclass(frozen=True, slots=True)
class LanguageDetection:
    """The result of automatic language detection.

    ``confidence`` is the probability reported by the model, in ``[0, 1]``.
    Dabuj surfaces a warning below :data:`LOW_CONFIDENCE_THRESHOLD` rather than
    silently trusting a weak guess.
    """

    language: Language
    confidence: float

    @property
    def is_confident(self) -> bool:
        return self.confidence >= LOW_CONFIDENCE_THRESHOLD


LOW_CONFIDENCE_THRESHOLD = 0.6
"""Below this, the UI warns that detection may be wrong.

Chosen conservatively: Whisper-family models report high probabilities on clean
speech, so a value this low almost always indicates genuinely ambiguous audio
(very short clips, music, or code-switching).
"""


__all__ = [
    "AUTO_DETECT",
    "LOW_CONFIDENCE_THRESHOLD",
    "Language",
    "LanguageDetection",
    "display_name",
]
