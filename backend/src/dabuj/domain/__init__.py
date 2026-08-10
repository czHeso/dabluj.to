"""Domain layer: the vocabulary of the application.

Everything here is a pure data structure or a pure function over one. The
domain knows nothing about FFmpeg, HTTP, AI runtimes or the filesystem, which
is what makes it cheap to test and safe to reuse from the CLI, the API and the
pipeline alike.
"""

from dabuj.domain.language import Language, LanguageDetection
from dabuj.domain.media import AudioStreamInfo, MediaInfo, VideoStreamInfo
from dabuj.domain.quality import Device, Precision, QualityProfile
from dabuj.domain.speaker import Speaker
from dabuj.domain.transcript import Segment, Transcript, TranscriptSource, Word

__all__ = [
    "AudioStreamInfo",
    "Device",
    "Language",
    "LanguageDetection",
    "MediaInfo",
    "Precision",
    "QualityProfile",
    "Segment",
    "Speaker",
    "Transcript",
    "TranscriptSource",
    "VideoStreamInfo",
    "Word",
]
