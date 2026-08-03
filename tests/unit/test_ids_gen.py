# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for tools/gen_ids.py (E1.2.1).

XRC control names are the single frozen registry (spec.md section
15b, R-05): ``ui/ids.py`` is generated from the ``.xrc`` files,
never typed by hand. These tests are that generator's specification,
written before ``tools/gen_ids.py`` existed.

``tools/`` is a dev-script tree, not an installed package (it has
no ``__init__.py`` and is excluded from ``[tool.setuptools.packages.
find]``), so the module under test is loaded from its file path
rather than imported by dotted name.
"""

import importlib.util
import re
import string
from pathlib import Path
from types import ModuleType  # noqa: TC003 -- used at runtime as a return type here

import pytest
from hypothesis import given
from hypothesis import strategies as st

_GEN_IDS_PATH = Path(__file__).resolve().parents[2] / "tools" / "gen_ids.py"


def _load_gen_ids(path: Path) -> ModuleType:
    """Load tools/gen_ids.py by path -- it isn't a package."""
    spec = importlib.util.spec_from_file_location("gen_ids", path)
    if spec is None or spec.loader is None:
        msg = f"could not build a module spec for {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen_ids = _load_gen_ids(_GEN_IDS_PATH)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ids"
HAPPY_DIR = FIXTURES / "happy"
DUPLICATE_DIR = FIXTURES / "duplicate"
SUFFIX_WARNING_DIR = FIXTURES / "suffix_warning"
INVALID_IDENTIFIER_DIR = FIXTURES / "invalid_identifier"
MENU_ITEMS_DIR = FIXTURES / "menu_items"

EXPECTED_HAPPY_BODY = (
    'EDIT_CROSSING_DLG = "edit_crossing_dlg"\n'
    'MAIN_FRAME = "main_frame"\n'
    'MANUAL_DEAL_DLG = "manual_deal_dlg"\n'
    'NEW_PLATE_INPUT = "new_plate_input"\n'
    'PLATE_INPUT = "plate_input"\n'
    'REASON_INPUT = "reason_input"\n'
    'REASSIGN_DLG = "reassign_dlg"\n'
    'RIDE_NAME_LBL = "ride_name_lbl"\n'
    'UNDO_BTN = "undo_btn"\n'
)


def test_render_ids_module_happy_fixture_names_render_as_sorted_constants() -> None:
    """A fixture .xrc produces the expected constants exactly.

    ``plate_input`` recurs in ``main_frame``, ``edit_crossing_dlg``
    and ``manual_deal_dlg``; ``reason_input`` recurs in three
    dialogs. Both collapse to one constant each here.
    """
    result = gen_ids.scan_xrc_directory(HAPPY_DIR)

    content = gen_ids.render_ids_module(result.names)

    assert content == f"{gen_ids._HEADER}\n\n{EXPECTED_HAPPY_BODY}"


def test_scan_xrc_directory_name_recurring_across_windows_collapses_without_raising() -> None:
    """A name shared by different top-level windows is legal.

    ``plate_input``/``reason_input`` each appear in three separate
    windows across the fixture; scanning must not raise, and each
    must collapse to exactly one entry in the result.
    """
    result = gen_ids.scan_xrc_directory(HAPPY_DIR)

    assert result.names == (
        "edit_crossing_dlg",
        "main_frame",
        "manual_deal_dlg",
        "new_plate_input",
        "plate_input",
        "reason_input",
        "reassign_dlg",
        "ride_name_lbl",
        "undo_btn",
    )


def test_scan_xrc_directory_duplicate_name_within_one_window_raises_duplicate_error() -> None:
    """The same control name used twice inside one window fails.

    Unlike cross-window recurrence, this is the illegal case: two
    ``plate_input`` controls both live under ``broken_dlg``.
    """
    with pytest.raises(
        gen_ids.DuplicateXrcNameError,
        match=re.escape("duplicate name(s) in 'broken_dlg'"),
    ):
        gen_ids.scan_xrc_directory(DUPLICATE_DIR)


def test_scan_xrc_directory_unknown_suffix_warns_and_known_suffixes_do_not() -> None:
    """A name with no recognised suffix warns; conventional ones don't.

    ``mystery_widget`` matches none of section 15b's suffixes;
    ``ok_btn``/``warn_dlg`` do, so only one warning is produced.
    """
    result = gen_ids.scan_xrc_directory(SUFFIX_WARNING_DIR)

    expected_warning = (
        f"'mystery_widget' does not end with a known suffix {gen_ids.KNOWN_SUFFIXES}"
    )
    assert result.warnings == (expected_warning,)
    assert result.names == ("mystery_widget", "ok_btn", "warn_dlg")


@pytest.mark.parametrize(
    "name",
    [
        "main_menubar",
        "main_statusbar",
        "audit_search",
        "about_logo_bmp",
        "gorba_link",
        "selftest_output",
        "main_splitter",
    ],
)
def test_suffix_warnings_canvas_only_suffix_produces_no_warning(name: str) -> None:
    """Canvas suffixes absent from section 15b's own list are known.

    Each of these appears as a backticked control name on the
    xrc-windows.md canvas but isn't in section 15b's suffix
    sentence; the generator must not flag any of them.
    """
    warnings = gen_ids._suffix_warnings([name])

    assert warnings == []


def test_scan_xrc_directory_menu_item_prefix_produces_constants_without_warning() -> None:
    """``mi_``-prefixed menu items get constants and never warn.

    Section 15b names ``mi_<action>`` as a prefix convention in the
    same sentence as the suffix list -- distinct from, not part of,
    the suffix check.
    """
    result = gen_ids.scan_xrc_directory(MENU_ITEMS_DIR)

    assert "mi_standings" in result.names
    assert "mi_zoom_100" in result.names
    assert result.warnings == ()


def test_suffix_warnings_menu_item_prefix_wins_over_unknown_suffix() -> None:
    """The ``mi_`` prefix rule takes precedence over an unknown tail.

    Pinned explicitly so a later reader doesn't reorder the check
    and start suffix-flagging menu items again.
    """
    warnings = gen_ids._suffix_warnings(["mi_totally_unconventional_tail"])

    assert warnings == []


def test_scan_xrc_directory_stock_ids_are_excluded_from_names() -> None:
    """wxID_OK and friends never become constants.

    The happy fixture's ``edit_crossing_dlg`` uses ``wxID_OK``/
    ``wxID_CANCEL`` for its button row (spec.md section 15b).
    """
    result = gen_ids.scan_xrc_directory(HAPPY_DIR)

    content = gen_ids.render_ids_module(result.names)

    assert "wxID_OK" not in result.names
    assert "WXID_OK" not in content


def test_scan_xrc_directory_empty_directory_produces_no_names_or_warnings(
    tmp_path: Path,
) -> None:
    """A directory with zero .xrc files scans to an empty result.

    This is the repo's own state today: no window has been authored
    yet under ``src/rivercrossing/ui/xrc/``, and ``nox -s ids_drift``
    runs against exactly this in CI right now.
    """
    result = gen_ids.scan_xrc_directory(tmp_path)

    assert result == gen_ids.ScanResult(names=(), warnings=())


def test_write_ids_module_called_twice_produces_byte_identical_output(tmp_path: Path) -> None:
    """Regeneration is idempotent: two writes match byte-for-byte."""
    out_path = tmp_path / "ids.py"

    gen_ids.write_ids_module(out_path, HAPPY_DIR)
    first_bytes = out_path.read_bytes()
    gen_ids.write_ids_module(out_path, HAPPY_DIR)
    second_bytes = out_path.read_bytes()

    assert first_bytes == second_bytes


def test_check_ids_module_stale_constant_with_no_xrc_name_reports_drift(tmp_path: Path) -> None:
    """A leftover constant with no matching .xrc name is drift."""
    out_path = tmp_path / "ids.py"
    result = gen_ids.scan_xrc_directory(HAPPY_DIR)
    stale_content = gen_ids.render_ids_module((*result.names, "stale_btn"))
    out_path.write_text(stale_content, encoding="utf-8")

    _, diffs = gen_ids.check_ids_module(out_path, HAPPY_DIR)

    assert diffs == ["constant has no xrc name: 'stale_btn'"]


def test_check_ids_module_missing_constant_for_xrc_name_reports_drift(tmp_path: Path) -> None:
    """An .xrc name with no matching constant is drift."""
    out_path = tmp_path / "ids.py"
    result = gen_ids.scan_xrc_directory(HAPPY_DIR)
    incomplete_names = tuple(name for name in result.names if name != "undo_btn")
    out_path.write_text(gen_ids.render_ids_module(incomplete_names), encoding="utf-8")

    _, diffs = gen_ids.check_ids_module(out_path, HAPPY_DIR)

    assert diffs == ["xrc name has no constant: 'undo_btn'"]


def test_check_ids_module_matching_constants_reports_no_drift(tmp_path: Path) -> None:
    """A freshly-written ids.py agrees with its own .xrc source."""
    out_path = tmp_path / "ids.py"
    gen_ids.write_ids_module(out_path, HAPPY_DIR)

    _, diffs = gen_ids.check_ids_module(out_path, HAPPY_DIR)

    assert diffs == []


def test_check_ids_module_missing_out_path_reports_every_name_as_drift(tmp_path: Path) -> None:
    """Before ids.py exists at all, every .xrc name is missing drift."""
    out_path = tmp_path / "ids.py"
    expected_names = gen_ids.scan_xrc_directory(HAPPY_DIR).names

    _, diffs = gen_ids.check_ids_module(out_path, HAPPY_DIR)

    assert diffs == [f"xrc name has no constant: {name!r}" for name in expected_names]


def test_render_ids_module_empty_names_produces_header_only() -> None:
    """An empty name collection renders just the module header."""
    content = gen_ids.render_ids_module(())

    assert content == f"{gen_ids._HEADER}\n"


def test_render_ids_module_single_name_produces_one_constant_line() -> None:
    """A single-name collection renders exactly one constant."""
    content = gen_ids.render_ids_module(("plate_input",))

    assert content == f'{gen_ids._HEADER}\n\nPLATE_INPUT = "plate_input"\n'


def test_render_ids_module_non_identifier_name_raises_invalid_error() -> None:
    """A name that can't become a Python identifier fails loudly."""
    result = gen_ids.scan_xrc_directory(INVALID_IDENTIFIER_DIR)

    with pytest.raises(
        gen_ids.InvalidXrcNameError,
        match=re.escape("'bad-name' is not a valid Python identifier"),
    ):
        gen_ids.render_ids_module(result.names)


_SUFFIX_STRATEGY = st.sampled_from(gen_ids.KNOWN_SUFFIXES)
_PREFIX_STRATEGY = st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=6)
_NAME_STRATEGY = st.builds(
    lambda prefix, suffix: f"{prefix}{suffix}", _PREFIX_STRATEGY, _SUFFIX_STRATEGY
)


@given(names=st.lists(_NAME_STRATEGY, min_size=1, max_size=5, unique=True))
def test_render_ids_module_round_trips_every_generated_name(names: list[str]) -> None:
    """Every rendered constant's value parses back out unchanged."""
    content = gen_ids.render_ids_module(names)

    parsed_values = gen_ids._parse_constant_values(content)

    assert parsed_values == set(names)


def test_main_write_flag_regenerates_ids_file_from_xrc_dir(tmp_path: Path) -> None:
    """``--write`` matches the nox ``gen_ids`` session's invocation."""
    out_path = tmp_path / "ids.py"

    exit_code = gen_ids.main(["--write", "--xrc-dir", str(HAPPY_DIR), "--out", str(out_path)])

    assert exit_code == 0
    assert out_path.read_text(encoding="utf-8") == f"{gen_ids._HEADER}\n\n{EXPECTED_HAPPY_BODY}"


def test_main_check_flag_returns_zero_when_no_drift(tmp_path: Path) -> None:
    """``--check`` matches the nox ``ids_drift`` session when clean."""
    out_path = tmp_path / "ids.py"
    gen_ids.write_ids_module(out_path, HAPPY_DIR)

    exit_code = gen_ids.main(["--check", "--xrc-dir", str(HAPPY_DIR), "--out", str(out_path)])

    assert exit_code == 0


def test_main_check_flag_returns_one_when_drift_detected(tmp_path: Path) -> None:
    """``--check`` fails the build when ids.py drifts (R-05)."""
    out_path = tmp_path / "ids.py"
    out_path.write_text(f'{gen_ids._HEADER}\n\nSTALE_BTN = "stale_btn"\n', encoding="utf-8")

    exit_code = gen_ids.main(["--check", "--xrc-dir", str(HAPPY_DIR), "--out", str(out_path)])

    assert exit_code == 1


def test_main_write_flag_returns_two_when_xrc_has_duplicate_name(tmp_path: Path) -> None:
    """A duplicate name fails at the CLI, not with a traceback."""
    out_path = tmp_path / "ids.py"

    exit_code = gen_ids.main(["--write", "--xrc-dir", str(DUPLICATE_DIR), "--out", str(out_path)])

    assert exit_code == 2
