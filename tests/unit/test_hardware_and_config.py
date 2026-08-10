"""Hardware profile recommendation, path confinement and settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from dabuj.config.paths import StoragePaths, is_within, resolve_within
from dabuj.config.settings import AppSettings, load_settings, save_settings
from dabuj.domain.language import Language, LanguageDetection
from dabuj.domain.quality import Device, Precision, QualityProfile
from dabuj.errors import ConfigurationError, UnsafePathError, ValidationError
from dabuj.hardware.detect import (
    AcceleratorInfo,
    CPUInfo,
    GPUInfo,
    GPUVendor,
    SystemInfo,
    detect_system,
)
from dabuj.hardware.profiles import recommend_profile

pytestmark = pytest.mark.unit

_GIB = 1024**3


def make_system(
    *,
    ram_gib: float = 16.0,
    cores: int = 8,
    vram_gib: float | None = None,
    cuda: bool = False,
    free_disk_gib: float = 100.0,
) -> SystemInfo:
    """Build a SystemInfo describing a hypothetical machine."""
    gpus: tuple[GPUInfo, ...] = ()
    if vram_gib is not None:
        gpus = (
            GPUInfo(
                name="Test GPU",
                vendor=GPUVendor.NVIDIA,
                total_memory_bytes=int(vram_gib * _GIB),
            ),
        )
    return SystemInfo(
        os_name="Linux",
        os_version="6.0",
        machine="x86_64",
        python_version="3.12.0",
        cpu=CPUInfo(
            name="Test CPU", architecture="x86_64", physical_cores=cores, logical_cores=cores * 2
        ),
        total_memory_bytes=int(ram_gib * _GIB),
        available_memory_bytes=int(ram_gib * _GIB * 0.7),
        gpus=gpus,
        accelerators=AcceleratorInfo(cuda=cuda),
        free_disk_bytes=int(free_disk_gib * _GIB),
    )


class TestProfileRecommendation:
    def test_office_laptop_gets_low(self) -> None:
        recommendation = recommend_profile(make_system(ram_gib=8, cores=4))

        assert recommendation.profile is QualityProfile.LOW
        assert recommendation.device is Device.CPU
        assert recommendation.precision is Precision.INT8

    def test_strong_cpu_only_machine_gets_balanced(self) -> None:
        """A good CPU deserves better than the floor, even with no GPU."""
        recommendation = recommend_profile(make_system(ram_gib=32, cores=12))

        assert recommendation.profile is QualityProfile.BALANCED
        assert recommendation.device is Device.CPU

    def test_mid_range_gpu_gets_balanced(self) -> None:
        recommendation = recommend_profile(make_system(ram_gib=16, cores=8, vram_gib=6, cuda=True))

        assert recommendation.profile is QualityProfile.BALANCED
        assert recommendation.device is Device.CUDA
        assert recommendation.precision is Precision.FLOAT16

    def test_capable_gpu_gets_high(self) -> None:
        recommendation = recommend_profile(make_system(ram_gib=32, cores=8, vram_gib=8, cuda=True))
        assert recommendation.profile is QualityProfile.HIGH

    def test_workstation_gets_ultra(self) -> None:
        recommendation = recommend_profile(
            make_system(ram_gib=64, cores=16, vram_gib=24, cuda=True)
        )
        assert recommendation.profile is QualityProfile.ULTRA

    def test_big_gpu_with_little_ram_is_not_ultra(self) -> None:
        """VRAM alone must not drive the recommendation."""
        recommendation = recommend_profile(make_system(ram_gib=8, cores=4, vram_gib=24, cuda=True))
        assert recommendation.profile is not QualityProfile.ULTRA

    def test_gpu_present_but_cuda_unavailable_is_treated_as_cpu(self) -> None:
        """A driver without a usable runtime cannot run inference."""
        recommendation = recommend_profile(
            make_system(ram_gib=32, cores=8, vram_gib=24, cuda=False)
        )

        assert recommendation.device is Device.CPU
        assert any("No usable GPU" in w for w in recommendation.warnings)

    def test_is_deterministic(self) -> None:
        system = make_system(ram_gib=16, cores=8, vram_gib=8, cuda=True)
        assert recommend_profile(system) == recommend_profile(system)

    def test_always_explains_itself(self) -> None:
        assert recommend_profile(make_system()).reasons

    def test_low_disk_warns(self) -> None:
        recommendation = recommend_profile(make_system(free_disk_gib=2))
        assert any("disk space" in w for w in recommendation.warnings)

    def test_low_memory_warns(self) -> None:
        recommendation = recommend_profile(make_system(ram_gib=4, cores=2))
        assert any("system memory" in w for w in recommendation.warnings)


class TestDetection:
    def test_detects_the_real_machine_without_raising(self) -> None:
        system = detect_system()

        assert system.total_memory_bytes > 0
        assert system.cpu.usable_threads >= 1
        assert Device.CPU in system.accelerators.available_devices

    def test_report_contains_nothing_identifying(self) -> None:
        """Privacy: no hostname, user name or serial number."""
        report = detect_system().to_dict()
        flattened = str(report).lower()

        for forbidden in ("serial", "hostname", "username", "macaddress"):
            assert forbidden not in flattened


class TestPathConfinement:
    def test_normal_relative_path_is_allowed(self, tmp_path: Path) -> None:
        assert resolve_within(tmp_path, "sub/file.txt") == (tmp_path / "sub/file.txt").resolve()

    @pytest.mark.parametrize(
        "attack",
        [
            "../escape.txt",
            "../../etc/passwd",
            "sub/../../escape.txt",
            "sub/../../../escape.txt",
        ],
    )
    def test_traversal_is_rejected(self, tmp_path: Path, attack: str) -> None:
        base = tmp_path / "base"
        base.mkdir()
        with pytest.raises(UnsafePathError):
            resolve_within(base, attack)

    def test_absolute_path_injection_is_rejected(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "outside.txt"
        with pytest.raises(UnsafePathError):
            resolve_within(base, str(outside))

    def test_the_base_itself_is_allowed(self, tmp_path: Path) -> None:
        assert resolve_within(tmp_path, ".") == tmp_path.resolve()

    def test_is_within_does_not_raise(self, tmp_path: Path) -> None:
        assert is_within(tmp_path, tmp_path / "a")
        assert not is_within(tmp_path / "base", tmp_path / "other")


class TestStoragePaths:
    def test_rooted_layout(self, tmp_path: Path) -> None:
        paths = StoragePaths.rooted_at(tmp_path)

        assert paths.models_dir == tmp_path / "models"
        assert paths.projects_dir == tmp_path / "projects"
        assert paths.log_file.parent == paths.logs_dir

    def test_ensure_creates_everything(self, tmp_path: Path) -> None:
        paths = StoragePaths.rooted_at(tmp_path / "new").ensure()

        assert all(directory.is_dir() for directory in paths.all_directories)
        assert paths.check_writable() == []

    def test_overrides_relocate_only_what_is_given(self, tmp_path: Path) -> None:
        paths = StoragePaths.rooted_at(tmp_path).with_overrides(models_dir=tmp_path / "elsewhere")

        assert paths.models_dir == tmp_path / "elsewhere"
        assert paths.projects_dir == tmp_path / "projects"


class TestSettings:
    def test_defaults_are_private_and_local(self) -> None:
        settings = AppSettings()

        assert settings.privacy.telemetry is False
        assert settings.privacy.allow_cloud_providers is False
        assert settings.server.host == "127.0.0.1"
        assert settings.server.allow_lan is False

    def test_lan_requires_explicit_opt_in(self) -> None:
        settings = AppSettings()
        assert settings.server.effective_host == "127.0.0.1"

        settings.server.allow_lan = True
        assert settings.server.effective_host == "0.0.0.0"  # noqa: S104

    def test_missing_file_yields_defaults(self, tmp_path: Path) -> None:
        assert load_settings(tmp_path / "absent.toml") == AppSettings()

    def test_round_trip(self, tmp_path: Path) -> None:
        target = tmp_path / "dabuj.toml"
        settings = AppSettings()
        settings.server.port = 9999
        settings.processing.quality = QualityProfile.HIGH

        save_settings(settings, target)
        loaded = load_settings(target)

        assert loaded.server.port == 9999
        assert loaded.processing.quality is QualityProfile.HIGH

    def test_malformed_toml_is_an_error_not_a_silent_default(self, tmp_path: Path) -> None:
        """Falling back silently would hide a typo that moves the models directory."""
        target = tmp_path / "bad.toml"
        target.write_text("this is not = = valid toml", encoding="utf-8")

        with pytest.raises(ConfigurationError, match="not valid TOML"):
            load_settings(target)

    def test_invalid_value_is_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "bad.toml"
        target.write_text("[server]\nport = 99999999\n", encoding="utf-8")

        with pytest.raises(ConfigurationError, match="invalid values"):
            load_settings(target)

    def test_save_is_atomic(self, tmp_path: Path) -> None:
        target = tmp_path / "dabuj.toml"
        save_settings(AppSettings(), target)

        assert target.exists()
        assert not list(tmp_path.glob("*.partial"))


class TestLanguage:
    @pytest.mark.parametrize(
        ("value", "code"),
        [("en", "en"), ("EN", "en"), ("cs_CZ", "cs-cz"), ("pt-BR", "pt-br"), ("Czech", "cs")],
    )
    def test_parsing_normalises(self, value: str, code: str) -> None:
        assert Language.parse(value).code == code

    def test_auto_is_recognised(self) -> None:
        assert Language.parse("auto").is_auto

    def test_base_code_strips_the_region(self) -> None:
        assert Language.parse("pt-BR").base_code == "pt"

    def test_known_codes_get_display_names(self) -> None:
        assert Language.parse("de").name == "German"

    def test_unknown_but_plausible_code_is_allowed(self) -> None:
        """The language list belongs to the model, not the application."""
        assert Language.parse("kab").code == "kab"

    @pytest.mark.parametrize("value", ["", "  ", "123", "e", "not a language!"])
    def test_nonsense_is_rejected(self, value: str) -> None:
        with pytest.raises(ValidationError):
            Language.parse(value)

    def test_low_confidence_is_flagged(self) -> None:
        assert not LanguageDetection(language=Language.parse("cs"), confidence=0.4).is_confident
        assert LanguageDetection(language=Language.parse("cs"), confidence=0.97).is_confident
