"""Cache keys, stage dependencies, cancellation and progress."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from dabuj.errors import CancelledError
from dabuj.pipeline.cache import (
    compute_cache_key,
    content_hash,
    file_fingerprint,
    stages_to_invalidate,
)
from dabuj.pipeline.cancellation import CancellationToken, NullCancellationToken
from dabuj.pipeline.progress import JobStatus, ProgressEvent, ProgressReporter, StageProgress
from dabuj.pipeline.stages import (
    STAGE_ORDER,
    Stage,
    StageState,
    dependencies_of,
    downstream_of,
    stages_up_to,
)

pytestmark = pytest.mark.unit


class TestStageGraph:
    def test_every_stage_is_ordered(self) -> None:
        assert set(STAGE_ORDER) == set(Stage)

    def test_dependencies_are_transitive(self) -> None:
        # ASR depends on audio extraction, which depends on the probe.
        assert dependencies_of(Stage.ASR) == {Stage.MEDIA_PROBE, Stage.AUDIO_EXTRACT}

    def test_dependencies_precede_their_dependents(self) -> None:
        """A stage can never depend on something that runs after it."""
        for stage in Stage:
            for dependency in dependencies_of(stage):
                assert dependency.order < stage.order, f"{stage} depends on later {dependency}"

    def test_translation_does_not_depend_on_diarization(self) -> None:
        """Re-running speaker detection must not discard translations."""
        assert Stage.DIARIZATION not in dependencies_of(Stage.TRANSLATION)

    def test_stages_up_to_is_a_prefix(self) -> None:
        assert stages_up_to(Stage.ASR) == (
            Stage.MEDIA_PROBE,
            Stage.AUDIO_EXTRACT,
            Stage.ASR,
        )


class TestInvalidation:
    def test_changing_translation_invalidates_only_downstream(self) -> None:
        """The headline incremental-processing requirement."""
        affected = stages_to_invalidate(Stage.TRANSLATION)

        assert affected == {Stage.TRANSLATION, Stage.TTS, Stage.MIX, Stage.MUX}
        assert Stage.ASR not in affected
        assert Stage.DIARIZATION not in affected
        assert Stage.AUDIO_EXTRACT not in affected

    def test_changing_the_source_invalidates_everything(self) -> None:
        assert stages_to_invalidate(Stage.MEDIA_PROBE) == set(Stage)

    def test_changing_tts_does_not_invalidate_translation(self) -> None:
        """Changing one speaker's voice must not force a retranslation."""
        affected = stages_to_invalidate(Stage.TTS)

        assert Stage.TRANSLATION not in affected
        assert affected == {Stage.TTS, Stage.MIX, Stage.MUX}

    def test_the_last_stage_invalidates_only_itself(self) -> None:
        assert stages_to_invalidate(Stage.MUX) == {Stage.MUX}

    def test_downstream_and_dependencies_are_consistent(self) -> None:
        for stage in Stage:
            for dependent in downstream_of(stage):
                assert stage in dependencies_of(dependent)


class TestCacheKeys:
    def test_identical_inputs_give_identical_keys(self) -> None:
        first = compute_cache_key(Stage.ASR, inputs={"audio": "abc"}, model_id="m")
        second = compute_cache_key(Stage.ASR, inputs={"audio": "abc"}, model_id="m")
        assert first == second

    def test_key_ordering_is_irrelevant(self) -> None:
        first = compute_cache_key(Stage.ASR, settings={"a": 1, "b": 2})
        second = compute_cache_key(Stage.ASR, settings={"b": 2, "a": 1})
        assert first == second

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"inputs": {"audio": "different"}},
            {"model_id": "other-model"},
            {"model_revision": "v2"},
            {"provider": "other"},
            {"provider_version": "9.9"},
            {"settings": {"beam": 9}},
        ],
    )
    def test_any_meaningful_change_changes_the_key(self, kwargs: dict) -> None:
        baseline = compute_cache_key(
            Stage.ASR,
            inputs={"audio": "abc"},
            model_id="m",
            model_revision="v1",
            provider="p",
            provider_version="1.0",
            settings={"beam": 5},
        )
        changed = compute_cache_key(
            Stage.ASR,
            **{
                "inputs": {"audio": "abc"},
                "model_id": "m",
                "model_revision": "v1",
                "provider": "p",
                "provider_version": "1.0",
                "settings": {"beam": 5},
                **kwargs,
            },
        )
        assert baseline != changed

    def test_different_stages_never_collide(self) -> None:
        keys = {compute_cache_key(stage, inputs={"x": "1"}).value for stage in Stage}
        assert len(keys) == len(list(Stage))

    def test_key_is_prefixed_with_the_stage(self) -> None:
        key = compute_cache_key(Stage.ASR)
        assert key.value.startswith(Stage.ASR.value)

    def test_paths_are_compared_by_name_not_location(self) -> None:
        """Moving a project must not invalidate its cache."""
        first = compute_cache_key(Stage.ASR, settings={"p": Path("/one/audio.wav")})
        second = compute_cache_key(Stage.ASR, settings={"p": Path("/two/audio.wav")})
        assert first == second


class TestFingerprints:
    def test_fingerprint_changes_when_content_changes(self, tmp_path: Path) -> None:
        target = tmp_path / "f.bin"
        target.write_bytes(b"a")
        before = file_fingerprint(target)

        time.sleep(0.01)
        target.write_bytes(b"much longer content")
        assert file_fingerprint(target) != before

    def test_missing_file_is_reported_not_raised(self, tmp_path: Path) -> None:
        assert file_fingerprint(tmp_path / "nope") == "missing"

    def test_content_hash_is_stable(self, tmp_path: Path) -> None:
        target = tmp_path / "f.bin"
        target.write_bytes(b"dabuj")
        assert content_hash(target) == content_hash(target)


class TestCancellation:
    def test_starts_uncancelled(self) -> None:
        assert not CancellationToken().is_cancelled

    def test_raises_after_cancel(self) -> None:
        token = CancellationToken()
        token.raise_if_cancelled()  # no-op

        token.cancel()
        with pytest.raises(CancelledError, match="was cancelled"):
            token.raise_if_cancelled("Transcription")

    def test_callbacks_run_on_cancel(self) -> None:
        token = CancellationToken()
        calls: list[str] = []
        token.on_cancel(lambda: calls.append("cleanup"))

        token.cancel()
        assert calls == ["cleanup"]

    def test_cancel_is_idempotent(self) -> None:
        token = CancellationToken()
        calls: list[int] = []
        token.on_cancel(lambda: calls.append(1))

        token.cancel()
        token.cancel()
        assert len(calls) == 1

    def test_registering_after_cancel_runs_immediately(self) -> None:
        """Closes the race where a subprocess starts just after a cancel."""
        token = CancellationToken()
        token.cancel()

        calls: list[int] = []
        token.on_cancel(lambda: calls.append(1))
        assert calls == [1]

    def test_unregister_prevents_the_callback(self) -> None:
        token = CancellationToken()
        calls: list[int] = []
        unregister = token.on_cancel(lambda: calls.append(1))

        unregister()
        token.cancel()
        assert calls == []

    def test_a_failing_callback_does_not_block_the_others(self) -> None:
        token = CancellationToken()
        calls: list[str] = []

        def _boom() -> None:
            raise RuntimeError("cleanup failed")

        token.on_cancel(_boom)
        token.on_cancel(lambda: calls.append("second"))

        token.cancel()
        assert calls == ["second"]

    def test_is_thread_safe(self) -> None:
        token = CancellationToken()
        calls: list[int] = []
        for _ in range(50):
            token.on_cancel(lambda: calls.append(1))

        threads = [threading.Thread(target=token.cancel) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(calls) == 50

    def test_null_token_never_cancels(self) -> None:
        token = NullCancellationToken()
        token.cancel()

        assert not token.is_cancelled
        token.raise_if_cancelled()


class TestProgress:
    def test_reports_stage_lifecycle(self) -> None:
        events: list[ProgressEvent] = []
        reporter = ProgressReporter("job1", stages=(Stage.ASR,), callback=events.append)

        reporter.job_started()
        reporter.stage_started(Stage.ASR)
        reporter.update(Stage.ASR, fraction=0.5, message="halfway")
        reporter.stage_finished(Stage.ASR)
        reporter.job_finished(JobStatus.COMPLETED)

        assert events[0].status is JobStatus.RUNNING
        assert any(e.progress == 0.5 and e.message == "halfway" for e in events)
        assert events[-1].status is JobStatus.COMPLETED

    def test_overall_progress_averages_stages(self) -> None:
        reporter = ProgressReporter("job", stages=(Stage.AUDIO_EXTRACT, Stage.ASR))
        reporter.job_started()

        reporter.stage_started(Stage.AUDIO_EXTRACT)
        reporter.stage_finished(Stage.AUDIO_EXTRACT)
        assert reporter.overall_fraction() == pytest.approx(0.5)

        reporter.stage_started(Stage.ASR)
        reporter.update(Stage.ASR, fraction=0.5)
        assert reporter.overall_fraction() == pytest.approx(0.75)

    def test_fraction_is_clamped(self) -> None:
        """Providers occasionally report slightly over 1.0."""
        events: list[ProgressEvent] = []
        reporter = ProgressReporter("job", stages=(Stage.ASR,), callback=events.append)
        reporter.stage_started(Stage.ASR)
        reporter.update(Stage.ASR, fraction=1.4)

        assert events[-1].progress == 1.0

    def test_a_failing_subscriber_does_not_break_the_job(self) -> None:
        """A disconnected browser must not lose an hour of processing."""
        reporter = ProgressReporter("job", stages=(Stage.ASR,))
        reporter.subscribe(lambda _: (_ for _ in ()).throw(RuntimeError("gone")))

        received: list[ProgressEvent] = []
        reporter.subscribe(received.append)

        reporter.stage_started(Stage.ASR)
        assert received

    def test_unsubscribe_stops_delivery(self) -> None:
        reporter = ProgressReporter("job", stages=(Stage.ASR,))
        events: list[ProgressEvent] = []
        unsubscribe = reporter.subscribe(events.append)

        reporter.stage_started(Stage.ASR)
        count = len(events)
        unsubscribe()
        reporter.update(Stage.ASR, fraction=0.9)

        assert len(events) == count

    def test_no_eta_before_there_is_evidence_for_one(self) -> None:
        """A wildly swinging ETA is worse than none."""
        progress = StageProgress(
            stage=Stage.ASR,
            state=StageState.RUNNING,
            fraction=0.01,
            started_at=time.monotonic(),
        )
        assert progress.eta_seconds is None

    def test_eta_appears_once_enough_work_is_done(self) -> None:
        progress = StageProgress(
            stage=Stage.ASR,
            state=StageState.RUNNING,
            fraction=0.5,
            started_at=time.monotonic() - 20.0,
        )
        eta = progress.eta_seconds

        assert eta is not None
        assert eta == pytest.approx(20.0, rel=0.3)

    def test_terminal_states(self) -> None:
        assert JobStatus.COMPLETED.is_terminal
        assert not JobStatus.RUNNING.is_terminal
        assert StageState.FAILED.is_terminal
        assert not StageState.PENDING.is_terminal
