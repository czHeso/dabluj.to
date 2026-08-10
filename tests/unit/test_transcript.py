"""Transcript model: editing, splitting, merging and the raw/edited invariant."""

from __future__ import annotations

import pytest

from dabuj.domain.transcript import Segment, Transcript, Word
from dabuj.errors import ValidationError

pytestmark = pytest.mark.unit


class TestSegmentText:
    def test_text_is_raw_until_edited(self) -> None:
        segment = Segment(start=0, end=1, raw_text="hello")
        assert segment.text == "hello"
        assert not segment.is_edited

    def test_editing_preserves_raw_text(self) -> None:
        """The whole point: an edit must never destroy the ASR output."""
        segment = Segment(start=0, end=1, raw_text="teh cat").with_edit("the cat")

        assert segment.text == "the cat"
        assert segment.raw_text == "teh cat"
        assert segment.is_edited

    def test_editing_back_to_original_clears_the_edit(self) -> None:
        segment = Segment(start=0, end=1, raw_text="hello").with_edit("goodbye")
        restored = segment.with_edit("hello")

        assert not restored.is_edited
        assert restored.edited_text is None

    def test_revert_discards_the_edit(self) -> None:
        segment = Segment(start=0, end=1, raw_text="hello").with_edit("goodbye")
        assert segment.revert_edit().text == "hello"

    def test_whitespace_is_normalised(self) -> None:
        assert Segment(start=0, end=1, raw_text="  padded  ").raw_text == "padded"


class TestSegmentTiming:
    def test_end_before_start_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="ends before it starts"):
            Segment(start=5.0, end=1.0)

    def test_negative_start_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            Segment(start=-1.0, end=1.0)

    def test_duration(self) -> None:
        assert Segment(start=1.25, end=4.0).duration == pytest.approx(2.75)

    def test_word_end_before_start_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="ends before it starts"):
            Word(text="x", start=2.0, end=1.0)


class TestSplit:
    def test_split_uses_word_timings(self) -> None:
        segment = Segment(
            start=0.0,
            end=4.0,
            raw_text="one two three four",
            words=[
                Word(text="one", start=0.0, end=1.0),
                Word(text="two", start=1.0, end=2.0),
                Word(text="three", start=2.0, end=3.0),
                Word(text="four", start=3.0, end=4.0),
            ],
        )
        left, right = segment.split_at(2.0)

        assert left.text == "one two"
        assert right.text == "three four"
        assert (left.start, left.end) == (0.0, 2.0)
        assert (right.start, right.end) == (2.0, 4.0)

    def test_split_keeps_the_original_id_on_the_left(self) -> None:
        """Existing references to the segment must remain valid."""
        segment = Segment(id="seg_x", start=0.0, end=4.0, raw_text="one two three four")
        left, right = segment.split_at(2.0)

        assert left.id == "seg_x"
        assert right.id != "seg_x"

    def test_split_without_word_timings_loses_no_words(self) -> None:
        segment = Segment(start=0.0, end=4.0, raw_text="alpha beta gamma delta")
        left, right = segment.split_at(2.0)

        assert f"{left.text} {right.text}".split() == segment.raw_text.split()

    def test_split_of_an_edited_segment_keeps_the_halves_edited(self) -> None:
        """Splitting must not resurrect pre-edit text in either half."""
        segment = Segment(start=0.0, end=4.0, raw_text="wrong words here now").with_edit(
            "right words here now"
        )
        left, right = segment.split_at(2.0)

        assert left.is_edited
        assert "wrong" not in left.text
        assert "wrong" not in right.text

    def test_split_drops_stale_translations(self) -> None:
        from dabuj.domain.transcript import Translation

        segment = Segment(
            start=0.0,
            end=4.0,
            raw_text="one two three four",
            translations={"cs": Translation(language="cs", text="jedna dva tři čtyři")},
        )
        left, right = segment.split_at(2.0)

        assert left.translations == {}
        assert right.translations == {}

    @pytest.mark.parametrize("timestamp", [0.0, 4.0, -1.0, 9.0])
    def test_split_outside_the_segment_is_rejected(self, timestamp: float) -> None:
        segment = Segment(start=0.0, end=4.0, raw_text="text")
        with pytest.raises(ValidationError, match="inside the segment"):
            segment.split_at(timestamp)


class TestMerge:
    def test_merge_joins_text_and_span(self) -> None:
        first = Segment(start=0.0, end=2.0, raw_text="Hello")
        second = Segment(start=2.0, end=4.0, raw_text="world")
        merged = first.merged_with(second)

        assert merged.text == "Hello world"
        assert (merged.start, merged.end) == (0.0, 4.0)

    def test_merge_keeps_the_first_id(self) -> None:
        first = Segment(id="seg_1", start=0.0, end=2.0, raw_text="a")
        second = Segment(id="seg_2", start=2.0, end=4.0, raw_text="b")
        assert first.merged_with(second).id == "seg_1"

    def test_merge_averages_confidence(self) -> None:
        first = Segment(start=0.0, end=2.0, raw_text="a", confidence=0.9)
        second = Segment(start=2.0, end=4.0, raw_text="b", confidence=0.5)
        assert first.merged_with(second).confidence == pytest.approx(0.7)

    def test_merge_concatenates_words(self) -> None:
        first = Segment(
            start=0.0, end=2.0, raw_text="a", words=[Word(text="a", start=0.0, end=2.0)]
        )
        second = Segment(
            start=2.0, end=4.0, raw_text="b", words=[Word(text="b", start=2.0, end=4.0)]
        )
        assert len(first.merged_with(second).words) == 2

    def test_merge_out_of_order_is_rejected(self) -> None:
        later = Segment(start=5.0, end=6.0, raw_text="b")
        earlier = Segment(start=0.0, end=1.0, raw_text="a")
        with pytest.raises(ValidationError, match="chronological order"):
            later.merged_with(earlier)


class TestTranscript:
    def test_duplicate_ids_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate segment id"):
            Transcript(
                segments=[
                    Segment(id="same", start=0, end=1, raw_text="a"),
                    Segment(id="same", start=1, end=2, raw_text="b"),
                ]
            )

    def test_split_segment_replaces_one_with_two(self, sample_transcript: Transcript) -> None:
        result = sample_transcript.split_segment("seg_a", 1.4)

        assert len(result) == 3
        assert [s.id for s in result.segments][0] == "seg_a"

    def test_merge_segments_replaces_two_with_one(self, sample_transcript: Transcript) -> None:
        result = sample_transcript.merge_segments("seg_a", "seg_b")

        assert len(result) == 1
        assert result.segments[0].text == "Good morning everybody Thanks for inviting me"

    def test_merging_a_segment_with_itself_is_rejected(self, sample_transcript: Transcript) -> None:
        with pytest.raises(ValidationError, match="cannot be merged with itself"):
            sample_transcript.merge_segments("seg_a", "seg_a")

    def test_unknown_segment_id_is_rejected(self, sample_transcript: Transcript) -> None:
        with pytest.raises(ValidationError, match="No segment with ID"):
            sample_transcript.index_of("seg_nope")

    def test_speaker_ids_in_order_of_appearance(self, sample_transcript: Transcript) -> None:
        assert sample_transcript.speaker_ids() == ["SPEAKER_00", "SPEAKER_01"]

    def test_rename_speaker_repoints_every_segment(self, sample_transcript: Transcript) -> None:
        renamed = sample_transcript.rename_speaker("SPEAKER_00", "anna")

        assert renamed.segments[0].speaker_id == "anna"
        assert renamed.segments[1].speaker_id == "SPEAKER_01"

    def test_speaking_duration_counts_overlaps_once(self) -> None:
        transcript = Transcript(
            segments=[
                Segment(start=0.0, end=5.0, raw_text="a"),
                Segment(start=3.0, end=8.0, raw_text="b"),
            ]
        )
        assert transcript.speaking_duration == pytest.approx(8.0)

    def test_low_confidence_excludes_edited_segments(self, sample_transcript: Transcript) -> None:
        """A human has already reviewed an edited segment."""
        flagged = sample_transcript.low_confidence_segments(0.5)
        assert [s.id for s in flagged] == ["seg_b"]

        edited = sample_transcript.replace_segment(
            sample_transcript.find("seg_b").with_edit("Corrected by hand")  # type: ignore[union-attr]
        )
        assert edited.low_confidence_segments(0.5) == []

    def test_transformations_do_not_mutate_the_original(
        self, sample_transcript: Transcript
    ) -> None:
        before = len(sample_transcript)
        sample_transcript.split_segment("seg_a", 1.0)
        assert len(sample_transcript) == before

    def test_json_round_trip_preserves_everything(self, sample_transcript: Transcript) -> None:
        restored = Transcript.model_validate_json(sample_transcript.model_dump_json())

        assert restored == sample_transcript
        assert restored.segments[0].words[0].confidence == pytest.approx(0.99)
