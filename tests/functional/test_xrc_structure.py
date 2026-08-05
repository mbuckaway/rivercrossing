# SPDX-License-Identifier: GPL-3.0-only
"""Structural checks over the authored .xrc resources (E1.3.1).

Pure XML -- no ``wx`` import and no display. These tests guard what
a loader test cannot see: that every frozen name from spec.md
section 15b is actually written into the file it belongs to, that no
name repeats inside one top-level window, that the two XRC classes
measured to drop or override their ``name`` never creep back in, and
that the canvas's radio defaults are declared where drawn.

Verification through the real toolkit -- ``LoadFrame`` /
``LoadDialog`` / ``LoadMenuBar``, ``FindWindowByName``, ``GetValue``
-- is a separate test. Nothing here may depend on a ``wx.App``.
"""

from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

pytestmark = pytest.mark.functional

XRC_DIR = Path(__file__).resolve().parents[2] / "src" / "rivercrossing" / "ui" / "xrc"

XRC_FILES = ("main.xrc", "setup.xrc")

# xrc-windows.md section A. main_menubar is the *menubar* resource's
# own name, so it is not one of the frame's controls. resume_infobar,
# reopened_infobar and finished_infobar are deliberately absent: XRC
# drops the name of a wxInfoBar, so they are built in code.
MAIN_FRAME_CONTROLS = (
    "ride_name_lbl",
    "ride_status_lbl",
    "clock_elapsed_lbl",
    "clock_remaining_lbl",
    "start_btn",
    "arm_stop_chk",
    "stop_btn",
    "plate_input",
    "record_btn",
    "last_crossing_lbl",
    "undo_btn",
    "main_splitter",
    "crossings_list",
    "crossings_count_lbl",
    "cards_count_lbl",
    "on_course_lbl",
    "shoe_lbl",
    "flagged_list",
    "review_btn",
    "main_statusbar",
)

# spec.md section 15b menu-item names, in spec.md section 15 row
# order. Stock ids sit where the platform expects them.
FILE_MENU_ITEMS = (
    "mi_new_ride",
    "mi_open_library",
    "mi_duplicate_ride",
    "mi_import_csv",
    "mi_export_csv",
    "mi_backup_now",
    "wxID_PREFERENCES",
    "wxID_EXIT",
)
RIDE_MENU_ITEMS = (
    "mi_start_ride",
    "mi_stop_ride",
    "mi_set_start_time",
    "mi_finish_ride",
    "mi_reopen_ride",
    "mi_audit_trail",
    "mi_ride_setup",
)
RIDERS_MENU_ITEMS = (
    "mi_rider_editor",
    "mi_add_entry",
    "mi_mark_dnf",
    "mi_entry_detail",
)
CARDS_MENU_ITEMS = (
    "mi_undo_crossing",
    "mi_add_crossing_at",
    "mi_edit_crossing",
    "mi_reassign_plate",
    "mi_deal_manual",
    "mi_void_card",
    "mi_review_held",
)
RESULTS_MENU_ITEMS = (
    "mi_standings",
    "mi_export_html",
    "mi_export_pdf",
    "mi_export_poster",
    "mi_export_results_csv",
    "mi_preview_browser",
    "mi_tiebreak_order",
)
THEME_MENU_ITEMS = ("mi_theme_system", "mi_theme_light", "mi_theme_dark")
ZOOM_MENU_ITEMS = (
    "mi_zoom_90",
    "mi_zoom_100",
    "mi_zoom_110",
    "mi_zoom_120",
    "mi_zoom_130",
    "mi_zoom_140",
    "mi_zoom_150",
)
VIEW_MENU_ITEMS = (*THEME_MENU_ITEMS, "mi_hide_times", *ZOOM_MENU_ITEMS)
HELP_MENU_ITEMS = ("mi_user_guide", "mi_shortcuts", "mi_selftest", "wxID_ABOUT")

MAIN_MENUBAR_CONTROLS = (
    *FILE_MENU_ITEMS,
    *RIDE_MENU_ITEMS,
    *RIDERS_MENU_ITEMS,
    *CARDS_MENU_ITEMS,
    *RESULTS_MENU_ITEMS,
    *VIEW_MENU_ITEMS,
    *HELP_MENU_ITEMS,
)

# xrc-windows.md section B: 22 annotated controls plus the stock
# button row.
RIDE_SETUP_CONTROLS = (
    "name_input",
    "date_picker",
    "start_time_picker",
    "venue_input",
    "lap_km_spin",
    "organizer_input",
    "scorer_input",
    "duration_input",
    "min_lap_input",
    "logo_picker",
    "solo_radio",
    "mixed_radio",
    "team_size_spin",
    "pooled_radio",
    "relay_radio",
    "decks_spin",
    "jokers_0_radio",
    "jokers_2_radio",
    "jokers_4_radio",
    "cap_chk",
    "cap_spin",
    "tiebreak_list",
    "wxID_OK",
    "wxID_CANCEL",
)

WINDOWS: dict[str, tuple[str, tuple[str, ...]]] = {
    "main_frame": ("main.xrc", MAIN_FRAME_CONTROLS),
    "main_menubar": ("main.xrc", MAIN_MENUBAR_CONTROLS),
    "ride_setup_dlg": ("setup.xrc", RIDE_SETUP_CONTROLS),
}
WINDOW_NAMES = ("main_frame", "main_menubar", "ride_setup_dlg")
DIALOG_NAMES = ("ride_setup_dlg",)

NAME_CASES = tuple(
    (window_name, control_name)
    for window_name in WINDOW_NAMES
    for control_name in WINDOWS[window_name][1]
)

MENU_LABELS = ("&File", "&Ride", "Ri&ders", "&Cards", "Re&sults", "&View", "&Help")

# spec.md section 15 has 38 rows: File 8, Ride 7, Riders 4, Cards 7,
# Results 7, View 1, Help 4. The single View row expands into the 11
# items section 15b names for it.
MENU_ITEM_COUNTS = (
    ("&File", 8),
    ("&Ride", 7),
    ("Ri&ders", 4),
    ("&Cards", 7),
    ("Re&sults", 7),
    ("&View", 11),
    ("&Help", 4),
)

ACCELERATOR_CASES = (
    ("mi_standings", "F5"),
    ("mi_user_guide", "F1"),
    ("mi_undo_crossing", "Ctrl+Z"),
)
ACCELERATED_ITEMS = ("mi_standings", "mi_undo_crossing", "mi_user_guide")

RADIO_MENU_ITEMS = (*THEME_MENU_ITEMS, *ZOOM_MENU_ITEMS)

# Canvas defaults, and the first member of each of the dialog's three
# radio groups (entry mode, plate model, jokers per deck).
SELECTED_RADIOS = ("solo_radio", "pooled_radio", "jokers_2_radio")
GROUP_OPENING_RADIOS = ("solo_radio", "pooled_radio", "jokers_0_radio")
GROUP_FOLLOWING_RADIOS = ("mixed_radio", "relay_radio", "jokers_2_radio", "jokers_4_radio")

FEED_LIST_NAMES = ("crossings_list", "flagged_list")

# The name wxDataViewListCtrl's XRC handler forces onto its control,
# discarding the authored one. It must never appear.
FORCED_DATAVIEW_NAME = "dataviewCtrl"


def _parse(filename: str) -> ET.Element:
    """Return the ``<resource>`` root element of one .xrc file."""
    return ET.parse(XRC_DIR / filename).getroot()  # noqa: S314 -- our own .xrc


def _top_level_windows(filename: str) -> dict[str, ET.Element]:
    """Map each top-level ``<object name=...>`` to its element."""
    return {child.attrib["name"]: child for child in _parse(filename) if child.tag == "object"}


def _window(window_name: str) -> ET.Element:
    """Return the top-level window element called *window_name*."""
    filename = WINDOWS[window_name][0]
    return _top_level_windows(filename)[window_name]


def _control_names_in(window: ET.Element) -> list[str]:
    """List every named ``<object>`` below *window*, excluding it."""
    return [
        obj.attrib["name"]
        for obj in window.iter("object")
        if "name" in obj.attrib and obj is not window
    ]


def _objects_by_name(window: ET.Element) -> dict[str, ET.Element]:
    """Map every named ``<object>`` in *window* to its element."""
    return {obj.attrib["name"]: obj for obj in window.iter("object") if "name" in obj.attrib}


def _classes_in(filename: str) -> list[str]:
    """List the ``class`` of every ``<object>`` in one .xrc file."""
    return [obj.attrib["class"] for obj in _parse(filename).iter("object")]


def _param(obj: ET.Element, tag: str) -> str:
    """Return the text of *obj*'s direct ``<tag>`` child, or ``""``."""
    child = obj.find(tag)
    return "" if child is None or child.text is None else child.text


def _menus() -> list[ET.Element]:
    """List the ``wxMenu`` children of main_menubar, in order."""
    return [child for child in _window("main_menubar") if child.attrib.get("class") == "wxMenu"]


def _menu_items(menu: ET.Element) -> list[ET.Element]:
    """List the ``wxMenuItem`` elements inside one menu."""
    return [obj for obj in menu.iter("object") if obj.attrib["class"] == "wxMenuItem"]


@pytest.mark.parametrize("filename", XRC_FILES)
def test_xrc_file_parses_as_xml_with_a_resource_root(filename: str) -> None:
    """Each authored file is well-formed XML with an XRC root."""
    root = _parse(filename)

    assert root.tag == "resource"


@pytest.mark.parametrize("window_name", WINDOW_NAMES)
def test_expected_window_is_declared_as_a_top_level_object(window_name: str) -> None:
    """LoadFrame/LoadDialog/LoadMenuBar need a top-level resource."""
    filename = WINDOWS[window_name][0]

    top_level = _top_level_windows(filename)

    assert window_name in top_level


@pytest.mark.parametrize(("window_name", "control_name"), NAME_CASES)
def test_frozen_name_is_declared_in_its_window(window_name: str, control_name: str) -> None:
    """Every frozen name is an ``<object name=...>`` (R-73)."""
    names = _control_names_in(_window(window_name))

    assert control_name in names


@pytest.mark.parametrize("window_name", WINDOW_NAMES)
def test_window_declares_exactly_the_frozen_name_set(window_name: str) -> None:
    """No stray names: ui/ids.py mirrors section 15b 1:1 (R-05)."""
    expected = WINDOWS[window_name][1]

    names = _control_names_in(_window(window_name))

    assert sorted(names) == sorted(expected)


@pytest.mark.parametrize("window_name", WINDOW_NAMES)
def test_window_declares_no_duplicate_control_name(window_name: str) -> None:
    """Section 15b: names are unique within their window."""
    counts = Counter(_control_names_in(_window(window_name)))

    repeated = sorted(name for name, count in counts.items() if count > 1)

    assert repeated == []


@pytest.mark.parametrize("filename", XRC_FILES)
def test_xrc_file_declares_no_dataviewlistctrl(filename: str) -> None:
    """Measured: its XRC handler discards the authored name."""
    classes = _classes_in(filename)

    assert classes.count("wxDataViewListCtrl") == 0


@pytest.mark.parametrize("filename", XRC_FILES)
def test_xrc_file_declares_no_infobar(filename: str) -> None:
    """Measured: XRC yields a generic Control and drops the name."""
    classes = _classes_in(filename)

    assert classes.count("wxInfoBar") == 0


@pytest.mark.parametrize("filename", XRC_FILES)
def test_xrc_file_never_uses_the_forced_dataview_name(filename: str) -> None:
    """The name wxDataViewListCtrl would impose must be absent."""
    names = [obj.attrib["name"] for obj in _parse(filename).iter("object") if "name" in obj.attrib]

    assert FORCED_DATAVIEW_NAME not in names


@pytest.mark.parametrize("list_name", FEED_LIST_NAMES)
def test_feed_list_is_declared_as_a_plain_dataviewctrl(list_name: str) -> None:
    """Only wxDataViewCtrl keeps its authored name through XRC."""
    control = _objects_by_name(_window("main_frame"))[list_name]

    assert control.attrib["class"] == "wxDataViewCtrl"


@pytest.mark.parametrize("dialog_name", DIALOG_NAMES)
def test_dialog_declares_a_std_dialog_button_sizer(dialog_name: str) -> None:
    """Stock button order comes from the platform, not from us."""
    classes = [obj.attrib["class"] for obj in _window(dialog_name).iter("object")]

    assert classes.count("wxStdDialogButtonSizer") == 1


def test_main_frame_declares_the_canvas_minimum_size() -> None:
    """Canvas footnote 1100x700 beats spec.md 13's 1180x740."""
    frame = _top_level_windows("main.xrc")["main_frame"]

    assert _param(frame, "size") == "1100,700"


def test_main_splitter_is_declared_as_a_splitter_window() -> None:
    """The control is authored here; only its sash comes from code."""
    splitter = _objects_by_name(_window("main_frame"))["main_splitter"]

    assert splitter.attrib["class"] == "wxSplitterWindow"


def test_main_frame_declares_a_three_field_status_bar() -> None:
    """Canvas: database name, last save, shoe cycle and seed."""
    status_bar = _objects_by_name(_window("main_frame"))["main_statusbar"]

    assert _param(status_bar, "fields") == "3"


def test_main_menubar_declares_the_seven_spec_menus_in_order() -> None:
    """spec.md 15: File, Ride, Riders, Cards, Results, View, Help."""
    labels = [_param(menu, "label") for menu in _menus()]

    assert tuple(labels) == MENU_LABELS


@pytest.mark.parametrize(("menu_label", "expected_items"), MENU_ITEM_COUNTS)
def test_menu_declares_the_expected_item_count(menu_label: str, expected_items: int) -> None:
    """Per-menu counts match the spec.md 15 row groups."""
    menu = next(one for one in _menus() if _param(one, "label") == menu_label)

    items = _menu_items(menu)

    assert len(items) == expected_items


def test_main_menubar_declares_forty_five_menu_item_names() -> None:
    """spec.md 15b names 45 ``mi_*`` items across the seven menus."""
    names = _control_names_in(_window("main_menubar"))

    menu_item_names = [name for name in names if name.startswith("mi_")]

    assert len(menu_item_names) == 45


def test_file_menu_declares_the_spec_15_row_order() -> None:
    """Import CSV, Export CSV, then Back Up -- spec.md 15's order."""
    file_menu = _menus()[0]

    names = [item.attrib["name"] for item in _menu_items(file_menu)]

    assert tuple(names) == FILE_MENU_ITEMS


@pytest.mark.parametrize(("item_name", "accelerator"), ACCELERATOR_CASES)
def test_menu_item_declares_its_accelerator(item_name: str, accelerator: str) -> None:
    """F5 standings, F1 user guide, Ctrl+Z undo (spec.md 15b)."""
    item = _objects_by_name(_window("main_menubar"))[item_name]

    assert _param(item, "accel") == accelerator


def test_only_the_documented_menu_items_declare_an_accelerator() -> None:
    """No invented shortcuts: exactly three items carry an ``accel``."""
    menubar = _window("main_menubar")

    accelerated = sorted(
        obj.attrib["name"] for obj in menubar.iter("object") if obj.find("accel") is not None
    )

    assert tuple(accelerated) == ACCELERATED_ITEMS


@pytest.mark.parametrize("item_name", RADIO_MENU_ITEMS)
def test_view_menu_radio_item_declares_the_radio_kind(item_name: str) -> None:
    """The theme trio and the seven zoom steps are radio items."""
    item = _objects_by_name(_window("main_menubar"))[item_name]

    assert _param(item, "radio") == "1"


def test_view_menu_hide_times_item_declares_the_check_kind() -> None:
    """A check item, which also keeps the radio groups apart."""
    item = _objects_by_name(_window("main_menubar"))["mi_hide_times"]

    assert _param(item, "checkable") == "1"


@pytest.mark.parametrize("radio_name", SELECTED_RADIOS)
def test_canvas_radio_default_declares_value_one(radio_name: str) -> None:
    """solo, pooled and jokers-2 start selected, exactly as drawn."""
    radio = _objects_by_name(_window("ride_setup_dlg"))[radio_name]

    assert _param(radio, "value") == "1"


@pytest.mark.parametrize("radio_name", GROUP_OPENING_RADIOS)
def test_radio_group_first_member_declares_rb_group(radio_name: str) -> None:
    """Each of the dialog's three groups is opened by wxRB_GROUP."""
    radio = _objects_by_name(_window("ride_setup_dlg"))[radio_name]

    assert "wxRB_GROUP" in _param(radio, "style")


@pytest.mark.parametrize("radio_name", GROUP_FOLLOWING_RADIOS)
def test_radio_group_later_member_omits_rb_group(radio_name: str) -> None:
    """A second wxRB_GROUP would split the group it belongs to."""
    radio = _objects_by_name(_window("ride_setup_dlg"))[radio_name]

    assert "wxRB_GROUP" not in _param(radio, "style")


def test_team_size_spin_declares_the_spec_documented_range() -> None:
    """spec.md 1 and 2: 2 to 10 riders per team, default 4."""
    spin = _objects_by_name(_window("ride_setup_dlg"))["team_size_spin"]

    bounds = (_param(spin, "min"), _param(spin, "max"), _param(spin, "value"))

    assert bounds == ("2", "10", "4")


# --------------------------------------------------------------------
# Phase 8: the record-crossing row and exit_confirm_dlg (xrc-windows.md
# amendments A2/P8-D1, P8-D3).


def test_entry_row_is_wrapped_in_a_record_crossing_static_box_sizer() -> None:
    """The operator's find-me frame: native, never custom-drawn."""
    labels = [
        _param(obj, "label")
        for obj in _window("main_frame").iter("object")
        if obj.attrib["class"] == "wxStaticBoxSizer"
    ]

    assert "Record crossing" in labels


def test_record_btn_declares_the_record_enter_label() -> None:
    """P8-D3: the row's key-hint convention, matching undo_btn's."""
    button = _objects_by_name(_window("main_frame"))["record_btn"]

    assert _param(button, "label") == "Record (Enter)"


def test_plate_input_declares_a_relative_sysfont_not_a_point_size() -> None:
    """P8-D3: relative only -- the 90-150% zoom must still apply."""
    font = _objects_by_name(_window("main_frame"))["plate_input"].find("font")

    values = (_param(font, "sysfont"), _param(font, "relativesize"), font.find("size"))

    assert values == ("wxSYS_DEFAULT_GUI_FONT", "1.5", None)


def test_plate_input_declares_a_hint_and_a_wider_size() -> None:
    """A7/P8-D3: the "Plate number" hint and a wider DIP width."""
    control = _objects_by_name(_window("main_frame"))["plate_input"]
    width = int(_param(control, "size").split(",")[0])

    assert (_param(control, "hint"), width >= 200) == ("Plate number", True)


def test_exit_confirm_dlg_is_declared_with_cancel_default_focused() -> None:
    """A2/P8-D1: the destructive confirm -- Cancel is safe & default."""
    dialog = _top_level_windows("dialogs.xrc")["exit_confirm_dlg"]
    sizer = next(
        obj for obj in dialog.iter("object") if obj.attrib["class"] == "wxStdDialogButtonSizer"
    )
    buttons = _objects_by_name(sizer)

    values = (
        _param(buttons["wxID_CANCEL"], "default"),
        _param(buttons["wxID_CANCEL"], "focused"),
        _param(buttons["wxID_OK"], "label"),
    )

    assert values == ("1", "1", "Quit")
