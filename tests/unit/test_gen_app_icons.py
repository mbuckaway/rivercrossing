# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for tools/gen_app_icons.py (Phase 8, task 8.7.2).

The generator rasterises the committed app icon (``.icns``), Windows
icon (``.ico``) and DMG background (``.tiff``) from their SVG sources
(``installers/branding/svg/*.svg``, P8-D5/P8-D6). ``tools/`` is a
dev-script tree, not an installed package (no ``__init__.py``, excluded
from ``[tool.setuptools.packages.find]``), so the module under test is
loaded from its file path -- the pattern test_ids_gen.py:28-39 already
established for ``tools/gen_ids.py``.
"""

import importlib.util
import re
import string
import subprocess
from pathlib import Path
from types import ModuleType  # noqa: TC003 -- used at runtime as a return type here
from unittest.mock import Mock

import pytest
from hypothesis import given
from hypothesis import strategies as st

_GEN_APP_ICONS_PATH = Path(__file__).resolve().parents[2] / "tools" / "gen_app_icons.py"


def _load_gen_app_icons(path: Path) -> ModuleType:
    """Load tools/gen_app_icons.py by path -- it isn't a package."""
    spec = importlib.util.spec_from_file_location("gen_app_icons", path)
    if spec is None or spec.loader is None:
        msg = f"could not build a module spec for {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen_app_icons = _load_gen_app_icons(_GEN_APP_ICONS_PATH)

EXPECTED_ICONSET_NAMES = (
    "icon_16x16.png",
    "icon_16x16@2x.png",
    "icon_32x32.png",
    "icon_32x32@2x.png",
    "icon_128x128.png",
    "icon_128x128@2x.png",
    "icon_256x256.png",
    "icon_256x256@2x.png",
    "icon_512x512.png",
    "icon_512x512@2x.png",
)

EXPECTED_ICONSET_PIXELS = (16, 32, 32, 64, 128, 256, 256, 512, 512, 1024)


def test_iconset_entries_names_the_ten_apple_iconset_files() -> None:
    """The .iconset carries exactly Apple's ten conventional names."""
    entries = gen_app_icons.iconset_entries()

    assert tuple(name for name, _pixels in entries) == EXPECTED_ICONSET_NAMES


def test_iconset_entries_render_retina_variants_at_double_pixels() -> None:
    """Each ``@2x`` name renders at double its 1x sibling's pixels."""
    entries = gen_app_icons.iconset_entries()

    assert tuple(pixels for _name, pixels in entries) == EXPECTED_ICONSET_PIXELS


def test_ico_sizes_span_sixteen_to_two_fifty_six() -> None:
    """The Windows .ico embeds every size from 16 through 256 pixels."""
    sizes = gen_app_icons.ico_sizes()

    assert sizes == (16, 24, 32, 48, 64, 128, 256)


def test_background_sizes_are_one_x_and_exactly_double() -> None:
    """The DMG background renders at 660x400 (1x) and 1320x800 (2x)."""
    one_x, two_x = gen_app_icons.background_sizes()

    assert (one_x, two_x) == ((660, 400), (1320, 800))


def test_render_commands_target_the_build_directory_never_the_tree(tmp_path: Path) -> None:
    """Every PNG the pipeline renders lives under the given build dir.

    GitLab forbids committing ``.png`` files (P8-D5) -- this is the
    hard rule as a test, over every path the tool itself computes.
    """
    build_dir = tmp_path / "build" / "branding"

    paths = gen_app_icons.png_output_paths(build_dir)

    assert all(path.is_relative_to(build_dir) for path in paths)


@given(segment=st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=8))
def test_png_output_paths_every_generated_build_dir_name_stays_under_it(segment: str) -> None:
    """For any build-dir name, every rendered PNG path stays under it.

    Pure path arithmetic (no filesystem access), so the invariant
    holds for an arbitrary build directory, not just the one fixed
    example above.
    """
    build_dir = Path("build") / segment

    paths = gen_app_icons.png_output_paths(build_dir)

    assert all(path.is_relative_to(build_dir) for path in paths)


def test_check_rsvg_convert_available_given_tool_present_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A present rsvg-convert passes the guard without raising."""
    monkeypatch.setattr(
        gen_app_icons.shutil, "which", lambda _name: "/opt/homebrew/bin/rsvg-convert"
    )

    result = gen_app_icons._check_rsvg_convert_available()

    assert result is None


def test_check_rsvg_convert_available_given_tool_missing_raises_rsvg_convert_missing_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing rsvg-convert raises, naming the brew install fix."""
    monkeypatch.setattr(gen_app_icons.shutil, "which", lambda _name: None)

    with pytest.raises(
        gen_app_icons.RsvgConvertMissingError,
        match=re.escape("brew install librsvg"),
    ):
        gen_app_icons._check_rsvg_convert_available()


def test_check_svg_sources_exist_given_both_files_present_does_not_raise(tmp_path: Path) -> None:
    """Both SVG sources present passes the guard without raising."""
    svg_dir = tmp_path / "svg"
    svg_dir.mkdir()
    (svg_dir / "icon.svg").write_text("<svg/>", encoding="utf-8")
    (svg_dir / "dmg_background.svg").write_text("<svg/>", encoding="utf-8")

    result = gen_app_icons._check_svg_sources_exist(tmp_path)

    assert result is None


def test_check_svg_sources_exist_given_background_svg_missing_raises_naming_it(
    tmp_path: Path,
) -> None:
    """A present icon.svg but a missing background names the latter.

    Exercises the loop's second iteration: the first file existing
    must not short-circuit the check for the second.
    """
    svg_dir = tmp_path / "svg"
    svg_dir.mkdir()
    (svg_dir / "icon.svg").write_text("<svg/>", encoding="utf-8")

    with pytest.raises(
        gen_app_icons.MissingSvgSourceError,
        match=re.escape("dmg_background.svg"),
    ):
        gen_app_icons._check_svg_sources_exist(tmp_path)


def test_main_given_missing_rsvg_convert_fails_naming_brew_install_librsvg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing rsvg-convert fails the CLI with the brew install hint."""
    monkeypatch.setattr(gen_app_icons.shutil, "which", lambda _name: None)

    exit_code = gen_app_icons.main(
        ["--branding-dir", str(tmp_path), "--build-dir", str(tmp_path / "build")]
    )

    assert exit_code == 1
    assert "brew install librsvg" in capsys.readouterr().err


def test_main_given_a_missing_svg_source_names_the_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty --branding-dir fails the CLI, naming the missing SVG."""
    monkeypatch.setattr(
        gen_app_icons.shutil, "which", lambda _name: "/opt/homebrew/bin/rsvg-convert"
    )
    empty_branding_dir = tmp_path / "branding"
    empty_branding_dir.mkdir()

    exit_code = gen_app_icons.main(
        ["--branding-dir", str(empty_branding_dir), "--build-dir", str(tmp_path / "build")]
    )

    assert exit_code == 1
    assert "icon.svg" in capsys.readouterr().err


def test_main_given_a_successful_pipeline_returns_zero_and_reports_the_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A raise-free pipeline exits 0 and names the target dir."""
    branding_dir = tmp_path / "branding"
    build_dir = tmp_path / "build"
    fake_generate = Mock(return_value=None)
    monkeypatch.setattr(gen_app_icons, "generate_branding", fake_generate)

    exit_code = gen_app_icons.main(
        ["--branding-dir", str(branding_dir), "--build-dir", str(build_dir)]
    )

    fake_generate.assert_called_once_with(branding_dir, build_dir)
    assert exit_code == 0
    assert str(branding_dir) in capsys.readouterr().out


# ------------------------------------------- subprocess timeout guard


def test_run_rsvg_convert_passes_timeout_60_and_creates_output_parent_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rsvg-convert runs with a 60s timeout (hung render)."""
    svg_path = tmp_path / "icon.svg"
    svg_path.write_text("<svg/>", encoding="utf-8")
    out_path = tmp_path / "nested" / "icon_16x16.png"
    mock_run = Mock(return_value=Mock())
    monkeypatch.setattr(gen_app_icons.subprocess, "run", mock_run)

    gen_app_icons._run_rsvg_convert(svg_path, out_path, (16, 16))

    assert out_path.parent.is_dir()
    mock_run.assert_called_once_with(
        [
            "rsvg-convert",
            "--width",
            "16",
            "--height",
            "16",
            "--output",
            str(out_path),
            str(svg_path),
        ],
        check=True,
        timeout=60,
    )


def test_run_rsvg_convert_timeout_propagates_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hung rsvg-convert raises TimeoutExpired; never swallowed."""
    svg_path = tmp_path / "icon.svg"
    svg_path.write_text("<svg/>", encoding="utf-8")
    out_path = tmp_path / "icon_16x16.png"

    def _run_times_out(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired("rsvg-convert", timeout=60)

    monkeypatch.setattr(gen_app_icons.subprocess, "run", _run_times_out)

    with pytest.raises(subprocess.TimeoutExpired, match=re.escape("rsvg-convert")):
        gen_app_icons._run_rsvg_convert(svg_path, out_path, (16, 16))


def test_run_iconutil_passes_timeout_60(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """/usr/bin/iconutil runs with a 60s timeout."""
    iconset_path = tmp_path / "AppIcon.iconset"
    icns_path = tmp_path / "RiverCrossing.icns"
    mock_run = Mock(return_value=Mock())
    monkeypatch.setattr(gen_app_icons.subprocess, "run", mock_run)

    gen_app_icons._run_iconutil(iconset_path, icns_path)

    mock_run.assert_called_once_with(
        ["/usr/bin/iconutil", "-c", "icns", "-o", str(icns_path), str(iconset_path)],
        check=True,
        timeout=60,
    )


def test_render_background_tiffutil_passes_timeout_60(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/usr/bin/tiffutil runs with a 60s timeout."""
    branding_dir = tmp_path / "branding"
    build_dir = tmp_path / "build"
    build_dir.mkdir(parents=True)
    svg_path = branding_dir / "svg" / "dmg_background.svg"
    svg_path.parent.mkdir(parents=True)
    svg_path.write_text("<svg/>", encoding="utf-8")
    one_x = build_dir / "dmg_background_660x400.png"
    two_x = build_dir / "dmg_background_1320x800.png"
    one_x.write_bytes(b"png")
    two_x.write_bytes(b"png")
    mock_run = Mock(return_value=Mock())
    monkeypatch.setattr(gen_app_icons.subprocess, "run", mock_run)

    gen_app_icons._render_background(branding_dir, build_dir)

    assert mock_run.call_count == 3
    assert all(call.kwargs["timeout"] == 60 for call in mock_run.call_args_list)
    mock_run.assert_called_with(
        [
            "/usr/bin/tiffutil",
            "-cathidpicheck",
            str(one_x),
            str(two_x),
            "-out",
            str(branding_dir / "dmg_background.tiff"),
        ],
        check=True,
        timeout=60,
    )
