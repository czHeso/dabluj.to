"""Project persistence, schema migration, model registry and FFmpeg argv."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dabuj.domain.quality import QualityProfile
from dabuj.domain.transcript import Transcript
from dabuj.errors import (
    NotFoundError,
    ProjectError,
    ProjectSchemaError,
    UnsafePathError,
    ValidationError,
)
from dabuj.media.ffmpeg import AudioExtractionSpec, FFmpegService
from dabuj.models.catalog import (
    BUILTIN_CATALOG,
    ModelTask,
    default_model_for,
    find_model,
    models_for_task,
)
from dabuj.models.registry import MARKER_FILENAME, ModelRegistry
from dabuj.pipeline.stages import Stage, StageState
from dabuj.projects.migrations import migrate
from dabuj.projects.schema import ProjectDocument, SourceMedia, StageRecord
from dabuj.projects.store import ProjectStore
from dabuj.version import PROJECT_SCHEMA_VERSION

pytestmark = pytest.mark.unit


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"not really a movie")
    return target


@pytest.fixture
def store(tmp_path: Path) -> ProjectStore:
    return ProjectStore(tmp_path / "projects")


class TestProjectStore:
    def test_create_builds_the_directory_layout(
        self, store: ProjectStore, media_file: Path
    ) -> None:
        project = store.create(media_file, name="My Film")

        assert project.manifest_path.is_file()
        assert project.source_path.is_file()
        assert project.cache_dir.is_dir()
        assert project.exports_dir.is_dir()
        assert project.name == "My Film"

    def test_name_defaults_to_the_filename(self, store: ProjectStore, media_file: Path) -> None:
        assert store.create(media_file).name == "movie"

    def test_media_is_copied_by_default(self, store: ProjectStore, media_file: Path) -> None:
        project = store.create(media_file)

        assert project.source_path != media_file
        assert project.source_path.read_bytes() == media_file.read_bytes()

    def test_media_can_be_referenced_in_place(self, store: ProjectStore, media_file: Path) -> None:
        """Avoids duplicating a 12 GB file."""
        project = store.create(media_file, import_media=False)
        assert project.source_path == media_file

    def test_missing_source_is_rejected(self, store: ProjectStore, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="does not exist"):
            store.create(tmp_path / "nope.mkv")

    def test_round_trip_preserves_the_transcript(
        self, store: ProjectStore, media_file: Path, sample_transcript: Transcript
    ) -> None:
        project = store.create(media_file)
        project.document.transcript = sample_transcript
        store.save(project)

        reopened = store.open(project.id)

        assert reopened.document.transcript == sample_transcript
        assert reopened.document.transcript.segments[0].words[0].text == "Good"

    def test_save_is_atomic(self, store: ProjectStore, media_file: Path) -> None:
        project = store.create(media_file)
        store.save(project)

        assert not list(project.directory.glob("*.partial"))

    def test_opening_an_unknown_project_is_a_clear_error(self, store: ProjectStore) -> None:
        with pytest.raises(NotFoundError, match="No project with the ID"):
            store.open("does-not-exist")

    def test_corrupt_manifest_is_reported_not_swallowed(
        self, store: ProjectStore, media_file: Path
    ) -> None:
        project = store.create(media_file)
        project.manifest_path.write_text("{ broken", encoding="utf-8")

        with pytest.raises(ProjectError, match="corrupt"):
            store.open(project.id)

    def test_listing_skips_a_corrupt_project(self, store: ProjectStore, media_file: Path) -> None:
        """One bad project must not hide all the others."""
        good = store.create(media_file, project_id="good")
        bad = store.create(media_file, project_id="bad")
        bad.manifest_path.write_text("{ broken", encoding="utf-8")

        listed = store.list_projects()
        assert [p.id for p in listed] == [good.id]

    @pytest.mark.parametrize("bad_id", ["../escape", "a/b", "a\\b", ""])
    def test_project_ids_cannot_escape_the_store(self, store: ProjectStore, bad_id: str) -> None:
        with pytest.raises((ValidationError, UnsafePathError)):
            store.open(bad_id)

    def test_cache_paths_are_confined(self, store: ProjectStore, media_file: Path) -> None:
        project = store.create(media_file)
        with pytest.raises(UnsafePathError):
            project.cache_path("audio", "../../escape.wav")

    def test_delete_removes_everything(self, store: ProjectStore, media_file: Path) -> None:
        project = store.create(media_file)
        store.delete(project.id)

        assert not project.directory.exists()

    def test_delete_can_keep_the_source_media(self, store: ProjectStore, media_file: Path) -> None:
        project = store.create(media_file)
        source = project.source_path
        store.delete(project.id, keep_source=True)

        assert source.is_file()
        assert not project.manifest_path.exists()

    def test_clear_cache_reclaims_space(self, store: ProjectStore, media_file: Path) -> None:
        project = store.create(media_file)
        audio = project.cache_path("audio", "a.wav")
        audio.write_bytes(b"x" * 2048)

        reclaimed = store.clear_cache(project.id)

        assert reclaimed >= 2048
        assert not audio.exists()
        assert audio.parent.is_dir()


class TestStageRecords:
    def test_a_stage_is_valid_only_with_a_matching_key(self) -> None:
        document = ProjectDocument(
            name="p", source=SourceMedia(relative_path="s.mkv", original_filename="s.mkv")
        )
        document.set_stage(Stage.ASR, StageRecord(state=StageState.COMPLETED, cache_key="key-1"))

        assert document.is_stage_valid(Stage.ASR, "key-1")
        assert not document.is_stage_valid(Stage.ASR, "key-2")

    def test_an_incomplete_stage_is_never_valid(self) -> None:
        document = ProjectDocument(
            name="p", source=SourceMedia(relative_path="s.mkv", original_filename="s.mkv")
        )
        document.set_stage(Stage.ASR, StageRecord(state=StageState.FAILED, cache_key="key-1"))
        assert not document.is_stage_valid(Stage.ASR, "key-1")

    def test_invalidate_resets_stages(self) -> None:
        document = ProjectDocument(
            name="p", source=SourceMedia(relative_path="s.mkv", original_filename="s.mkv")
        )
        document.set_stage(Stage.ASR, StageRecord(state=StageState.COMPLETED, cache_key="k"))
        document.invalidate(frozenset({Stage.ASR}))

        assert document.stages[Stage.ASR].state is StageState.PENDING

    def test_completed_stages_are_returned_in_pipeline_order(self) -> None:
        document = ProjectDocument(
            name="p", source=SourceMedia(relative_path="s.mkv", original_filename="s.mkv")
        )
        document.set_stage(Stage.ASR, StageRecord(state=StageState.COMPLETED))
        document.set_stage(Stage.MEDIA_PROBE, StageRecord(state=StageState.COMPLETED))

        assert document.completed_stages == (Stage.MEDIA_PROBE, Stage.ASR)

    def test_warnings_are_deduplicated(self) -> None:
        document = ProjectDocument(
            name="p", source=SourceMedia(relative_path="s.mkv", original_filename="s.mkv")
        )
        document.add_warning("same")
        document.add_warning("same")

        assert document.warnings == ["same"]


class TestMigrations:
    def test_current_version_passes_through(self) -> None:
        document = {"schema_version": PROJECT_SCHEMA_VERSION, "name": "p"}
        assert migrate(document) == document

    def test_a_newer_project_is_refused_not_guessed_at(self) -> None:
        with pytest.raises(ProjectSchemaError, match="newer version of Dabuj"):
            migrate({"schema_version": PROJECT_SCHEMA_VERSION + 5})

    def test_a_missing_version_is_refused(self) -> None:
        with pytest.raises(ProjectSchemaError, match="missing its schema version"):
            migrate({"name": "p"})

    def test_migration_does_not_mutate_its_input(self) -> None:
        document = {"schema_version": PROJECT_SCHEMA_VERSION, "name": "p"}
        migrate(document)
        assert document == {"schema_version": PROJECT_SCHEMA_VERSION, "name": "p"}

    def test_saved_projects_record_their_schema_version(
        self, store: ProjectStore, media_file: Path
    ) -> None:
        project = store.create(media_file)
        payload = json.loads(project.manifest_path.read_text(encoding="utf-8"))

        assert payload["schema_version"] == PROJECT_SCHEMA_VERSION


class TestCatalog:
    def test_every_entry_declares_a_license(self) -> None:
        for spec in BUILTIN_CATALOG:
            assert spec.license and spec.license != "unknown", spec.id

    def test_no_model_is_marked_redistributable(self) -> None:
        """Dabuj bundles no weights, so it never claims redistribution rights."""
        assert all(not spec.redistribution for spec in BUILTIN_CATALOG)

    def test_model_ids_are_unique(self) -> None:
        ids = [spec.id for spec in BUILTIN_CATALOG]
        assert len(ids) == len(set(ids))

    def test_asr_models_cover_the_target_languages(self) -> None:
        for spec in models_for_task(ModelTask.ASR):
            for code in ("en", "de", "cs"):
                assert spec.supports_language(code), f"{spec.id} lacks {code}"

    def test_lookup(self) -> None:
        assert find_model("whisper-small") is not None
        assert find_model("nope") is None

    def test_a_default_exists_for_every_profile(self) -> None:
        for profile in QualityProfile:
            assert default_model_for(ModelTask.ASR, profile) is not None

    def test_size_labels_are_marked_approximate(self) -> None:
        assert find_model("whisper-small").approx_size_label.startswith("~")  # type: ignore[union-attr]


class TestModelRegistry:
    def test_nothing_is_installed_initially(self, tmp_path: Path) -> None:
        registry = ModelRegistry(tmp_path / "models")

        assert registry.list_installed() == ()
        assert not registry.is_installed("whisper-small")

    def test_unknown_model_is_a_clear_error(self, tmp_path: Path) -> None:
        with pytest.raises(NotFoundError, match="no model called"):
            ModelRegistry(tmp_path).spec_for("no-such-model")

    def test_path_for_an_uninstalled_model_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(NotFoundError, match="not installed"):
            ModelRegistry(tmp_path).path_for("whisper-small")

    def test_a_directory_without_a_marker_is_not_installed(self, tmp_path: Path) -> None:
        """Half-downloaded weights must never look like a working model."""
        models = tmp_path / "models"
        (models / "whisper-small").mkdir(parents=True)
        (models / "whisper-small" / "model.bin").write_bytes(b"partial")

        registry = ModelRegistry(models)
        assert not registry.is_installed("whisper-small")
        assert registry.list_installed() == ()

    def test_a_marker_makes_it_installed(self, tmp_path: Path) -> None:
        models = tmp_path / "models"
        directory = models / "whisper-small"
        directory.mkdir(parents=True)
        (directory / MARKER_FILENAME).write_text(
            json.dumps({"model_id": "whisper-small", "revision": "main", "size_bytes": 42}),
            encoding="utf-8",
        )

        registry = ModelRegistry(models)
        installed = registry.get("whisper-small")

        assert installed is not None
        assert installed.size_bytes == 42
        assert not installed.is_orphaned

    def test_a_model_outside_the_catalog_is_reported_as_orphaned(self, tmp_path: Path) -> None:
        models = tmp_path / "models"
        directory = models / "retired-model"
        directory.mkdir(parents=True)
        (directory / MARKER_FILENAME).write_text(
            json.dumps({"model_id": "retired-model", "revision": "main"}), encoding="utf-8"
        )

        installed = ModelRegistry(models).get("retired-model")
        assert installed is not None
        assert installed.is_orphaned

    def test_removing_an_uninstalled_model_is_a_clear_error(self, tmp_path: Path) -> None:
        with pytest.raises(NotFoundError):
            ModelRegistry(tmp_path).remove("whisper-small")

    def test_an_unreadable_marker_is_treated_as_not_installed(self, tmp_path: Path) -> None:
        models = tmp_path / "models"
        directory = models / "whisper-small"
        directory.mkdir(parents=True)
        (directory / MARKER_FILENAME).write_text("{ broken", encoding="utf-8")

        assert ModelRegistry(models).get("whisper-small") is None


class TestFFmpegCommand:
    """The argv is built without invoking FFmpeg, so these are pure unit tests."""

    def test_produces_asr_ready_audio(self, tmp_path: Path) -> None:
        args = FFmpegService.build_extract_command(
            tmp_path / "in.mkv", tmp_path / "out.wav", AudioExtractionSpec()
        )

        assert "-ar" in args and args[args.index("-ar") + 1] == "16000"
        assert "-ac" in args and args[args.index("-ac") + 1] == "1"
        assert "pcm_s16le" in args

    def test_video_and_subtitles_are_excluded(self, tmp_path: Path) -> None:
        args = FFmpegService.build_extract_command(
            tmp_path / "in.mkv", tmp_path / "out.wav", AudioExtractionSpec()
        )
        assert {"-vn", "-sn", "-dn"} <= set(args)

    def test_stream_selection(self, tmp_path: Path) -> None:
        args = FFmpegService.build_extract_command(
            tmp_path / "in.mkv", tmp_path / "out.wav", AudioExtractionSpec(stream_index=2)
        )
        assert args[args.index("-map") + 1] == "0:a:2"

    def test_seek_comes_before_the_input(self, tmp_path: Path) -> None:
        """-ss before -i is the fast path: FFmpeg seeks instead of decoding."""
        args = FFmpegService.build_extract_command(
            tmp_path / "in.mkv", tmp_path / "out.wav", AudioExtractionSpec(start=30.0)
        )
        assert args.index("-ss") < args.index("-i")

    def test_normalisation_is_opt_in(self, tmp_path: Path) -> None:
        plain = FFmpegService.build_extract_command(
            tmp_path / "in.mkv", tmp_path / "out.wav", AudioExtractionSpec()
        )
        assert "-af" not in plain

        normalised = FFmpegService.build_extract_command(
            tmp_path / "in.mkv", tmp_path / "out.wav", AudioExtractionSpec(normalize=True)
        )
        assert "loudnorm" in normalised[normalised.index("-af") + 1]

    def test_arguments_are_a_list_so_filenames_cannot_inject(self, tmp_path: Path) -> None:
        """Passing an argv array is what makes shell metacharacters inert."""
        nasty = tmp_path / "a; rm -rf ~ && echo $(whoami).mkv"
        args = FFmpegService.build_extract_command(
            nasty, tmp_path / "out.wav", AudioExtractionSpec()
        )

        assert isinstance(args, list)
        assert str(nasty) in args
        assert all(isinstance(arg, str) for arg in args)

    def test_progress_is_requested(self, tmp_path: Path) -> None:
        args = FFmpegService.build_extract_command(
            tmp_path / "in.mkv", tmp_path / "out.wav", AudioExtractionSpec()
        )
        assert args[args.index("-progress") + 1] == "pipe:1"

    def test_spec_fingerprint_reflects_settings(self) -> None:
        assert (
            AudioExtractionSpec().cache_fingerprint()
            != AudioExtractionSpec(normalize=True).cache_fingerprint()
        )
