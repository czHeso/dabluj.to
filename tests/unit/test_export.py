"""Subtitle and text exporters.

The format details tested here (comma vs period, cue numbering, the WEBVTT
header) are exactly the ones that break players silently when wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dabuj.domain.speaker import Speaker
from dabuj.domain.transcript import Segment, Transcript, Translation
from dabuj.errors import ValidationError
from dabuj.export.formats import ExportFormat, export_transcript, write_transcript
from dabuj.export.subtitles import srt_timestamp, to_srt, to_vtt, vtt_timestamp
from dabuj.export.text import to_json, to_text

pytestmark = pytest.mark.unit


class TestTimestamps:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0.0, "00:00:00,000"),
            (1.5, "00:00:01,500"),
            (61.25, "00:01:01,250"),
            (3661.007, "01:01:01,007"),
            (7200.0, "02:00:00,000"),
        ],
    )
    def test_srt_uses_a_comma(self, seconds: float, expected: str) -> None:
        assert srt_timestamp(seconds) == expected

    def test_vtt_uses_a_period(self) -> None:
        assert vtt_timestamp(61.25) == "00:01:01.250"

    def test_negative_time_clamps_to_zero(self) -> None:
        assert srt_timestamp(-5.0) == "00:00:00,000"

    def test_milliseconds_are_rounded_not_truncated(self) -> None:
        assert srt_timestamp(1.0006) == "00:00:01,001"


class TestSrt:
    def test_cue_numbering_starts_at_one(self, sample_transcript: Transcript) -> None:
        lines = to_srt(sample_transcript).splitlines()
        assert lines[0] == "1"
        assert "2" in to_srt(sample_transcript)

    def test_arrow_format(self, sample_transcript: Transcript) -> None:
        assert "00:00:00,000 --> 00:00:02,500" in to_srt(sample_transcript)

    def test_blank_segments_are_skipped_and_numbering_stays_contiguous(self) -> None:
        transcript = Transcript(
            segments=[
                Segment(start=0, end=1, raw_text="first"),
                Segment(start=1, end=2, raw_text=""),
                Segment(start=2, end=3, raw_text="second"),
            ]
        )
        output = to_srt(transcript)

        assert output.startswith("1\n")
        assert "\n2\n" in output
        assert "3" not in output.split("-->")[0]

    def test_speaker_prefix_is_opt_in(self, sample_transcript: Transcript) -> None:
        assert "SPEAKER_00:" not in to_srt(sample_transcript)
        assert "SPEAKER_00:" in to_srt(sample_transcript, include_speakers=True)

    def test_speaker_display_names_are_used(self, sample_transcript: Transcript) -> None:
        speakers = {"SPEAKER_00": Speaker(id="SPEAKER_00", display_name="Anna")}
        output = to_srt(sample_transcript, speakers=speakers, include_speakers=True)

        assert "Anna:" in output
        # The second speaker has no display name, so its ID is used.
        assert "SPEAKER_01:" in output

    def test_empty_transcript_produces_empty_output(self) -> None:
        assert to_srt(Transcript()) == ""

    def test_translation_is_used_when_requested(self) -> None:
        transcript = Transcript(
            segments=[
                Segment(
                    start=0,
                    end=1,
                    raw_text="Hello",
                    translations={"cs": Translation(language="cs", text="Ahoj")},
                )
            ]
        )
        assert "Ahoj" in to_srt(transcript, language="cs")

    def test_untranslated_segments_fall_back_rather_than_vanish(self) -> None:
        """Dropping them would desynchronise the subtitle track from the audio."""
        transcript = Transcript(
            segments=[
                Segment(start=0, end=1, raw_text="Hello"),
                Segment(
                    start=1,
                    end=2,
                    raw_text="World",
                    translations={"cs": Translation(language="cs", text="Světe")},
                ),
            ]
        )
        output = to_srt(transcript, language="cs")

        assert "Hello" in output
        assert "Světe" in output

    def test_adapted_translation_wins_for_dubbing(self) -> None:
        transcript = Transcript(
            segments=[
                Segment(
                    start=0,
                    end=1,
                    raw_text="Hello there",
                    translations={
                        "cs": Translation(language="cs", text="Dobrý den", adapted_text="Ahoj")
                    },
                )
            ]
        )
        assert "Ahoj" in to_srt(transcript, language="cs")


class TestVtt:
    def test_starts_with_the_header(self, sample_transcript: Transcript) -> None:
        assert to_vtt(sample_transcript).startswith("WEBVTT\n")

    def test_has_no_cue_numbers(self, sample_transcript: Transcript) -> None:
        body = to_vtt(sample_transcript).split("\n\n", 1)[1]
        assert not body.lstrip().startswith("1\n")

    def test_speakers_use_voice_spans(self, sample_transcript: Transcript) -> None:
        assert "<v SPEAKER_00>" in to_vtt(sample_transcript, include_speakers=True)

    def test_empty_transcript_still_has_a_header(self) -> None:
        assert to_vtt(Transcript()).strip() == "WEBVTT"


class TestText:
    def test_groups_consecutive_segments_by_speaker(self) -> None:
        transcript = Transcript(
            segments=[
                Segment(start=0, end=1, raw_text="One", speaker_id="A"),
                Segment(start=1, end=2, raw_text="Two", speaker_id="A"),
                Segment(start=2, end=3, raw_text="Three", speaker_id="B"),
            ]
        )
        output = to_text(transcript)

        assert output.count("A:") == 1
        assert output.count("B:") == 1

    def test_timestamps_can_be_disabled(self, sample_transcript: Transcript) -> None:
        assert "[0:00]" not in to_text(sample_transcript, include_timestamps=False)


class TestJson:
    def test_includes_derived_fields(self, sample_transcript: Transcript) -> None:
        payload = json.loads(to_json(sample_transcript))
        segment = payload["segments"][0]

        assert segment["text"] == "Good morning everybody"
        assert segment["raw_text"] == "Good morning everybody"
        assert segment["is_edited"] is False
        assert len(segment["words"]) == 3

    def test_preserves_both_raw_and_edited_text(self) -> None:
        transcript = Transcript(segments=[Segment(start=0, end=1, raw_text="teh").with_edit("the")])
        segment = json.loads(to_json(transcript))["segments"][0]

        assert segment["raw_text"] == "teh"
        assert segment["edited_text"] == "the"
        assert segment["is_edited"] is True

    def test_metadata_is_included(self, sample_transcript: Transcript) -> None:
        payload = json.loads(to_json(sample_transcript, metadata={"project_id": "abc"}))
        assert payload["metadata"]["project_id"] == "abc"

    def test_non_ascii_is_not_escaped(self) -> None:
        transcript = Transcript(segments=[Segment(start=0, end=1, raw_text="Příliš žluťoučký")])
        assert "Příliš žluťoučký" in to_json(transcript)


class TestFormatDispatch:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("srt", ExportFormat.SRT),
            ("SRT", ExportFormat.SRT),
            (".srt", ExportFormat.SRT),
            ("webvtt", ExportFormat.VTT),
            ("subrip", ExportFormat.SRT),
            ("text", ExportFormat.TXT),
        ],
    )
    def test_parse_accepts_aliases(self, value: str, expected: ExportFormat) -> None:
        assert ExportFormat.parse(value) is expected

    def test_unknown_format_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not a supported export format"):
            ExportFormat.parse("docx")

    @pytest.mark.parametrize("fmt", list(ExportFormat))
    def test_every_format_renders(self, fmt: ExportFormat, sample_transcript: Transcript) -> None:
        assert export_transcript(sample_transcript, fmt).strip()

    def test_write_is_utf8_and_atomic(self, tmp_path: Path, sample_transcript: Transcript) -> None:
        target = tmp_path / "nested" / "out.srt"
        write_transcript(sample_transcript, ExportFormat.SRT, target)

        assert target.exists()
        assert not list(tmp_path.rglob("*.partial"))
        assert "Good morning" in target.read_text(encoding="utf-8")
