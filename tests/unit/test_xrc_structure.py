# SPDX-License-Identifier: GPL-3.0-only
"""Structural checks over the authored .xrc resources (E1.3.1).

Pure XML -- no ``wx`` import and no display. These tests guard what
a loader test cannot see: that every frozen name from spec.md
section 15b is actually written into the file it belongs to, that no
name repeats inside one top-level window, that the two XRC classes
measured to drop or override their ``name`` never creep back in, that
the canvas's radio defaults are declared where drawn, and that the
menubar's authored items are exactly ``commands.ROUTE_TABLE``'s
routes (a wx-free import, so the no-display rule still holds).

Verification through the real toolkit -- ``LoadFrame`` /
``LoadDialog`` / ``LoadMenuBar``, ``FindWindowByName``, ``GetValue``
-- is a separate test. Nothing here may depend on a ``wx.App``.
"""

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from defusedxml.ElementTree import parse

from rivercrossing.ui import commands

if TYPE_CHECKING:
    # Type-only: defusedxml does not re-export the Element class.
    # Every parse goes through the defused facade above.
    from xml.etree.ElementTree import Element

XRC_DIR = Path(__file__).resolve().parents[2] / "src" / "rivercrossing" / "ui" / "xrc"

XRC_FILES = ("main.xrc", "setup.xrc", "settings.xrc")

# xrc-windows.md section A. main_menubar is the *menubar* resource's
# own name, so it is not one of the frame's controls. The console
# declares no ``wxInfoBar`` at all: XRC drops its name, and G4 retired
# the two code-side bars that once stood in for one.
MAIN_FRAME_CONTROLS = (
    "ride_logo_bmp",
    "ride_name_value",
    "ride_date_value",
    "ride_venue_value",
    "ride_organizer_value",
    "ride_scorer_value",
    "ride_lap_km_value",
    "ride_status_lbl",
    "ride_status_panel",
    # Phase 6: the header's two-digit Current Lap reading.
    "current_lap_lbl",
    "clock_elapsed_lbl",
    "clock_remaining_lbl",
    "elapsed_clock_panel",
    "remaining_clock_panel",
    "start_btn",
    "stop_btn",
    "plate_input",
    "record_btn",
    "last_crossing_lbl",
    "undo_btn",
    "main_splitter",
    "crossings_list",
    # Phase 4: the crossings search box above the list.
    "crossings_search",
    "crossings_count_lbl",
    "cards_count_lbl",
    "on_course_lbl",
    "shoe_lbl",
    "riders_count_lbl",
    "teams_count_lbl",
    "review_notebook",
    "flagged_list",
    "show_held_only_chk",
    "review_btn",
    "console_riders_list",
    "main_statusbar",
)

# spec.md section 15b menu-item names, in spec.md section 15 row
# order. Stock ids sit where the platform expects them.
FILE_MENU_ITEMS = (
    "mi_open_library",
    "mi_duplicate_ride",
    "mi_import_csv",
    "mi_export_csv",
    "mi_backup_now",
    "mi_simulation",
    "wxID_PREFERENCES",
    "wxID_EXIT",
)
# D1: the ride-lifecycle rows live in ONE menu. mi_new_ride moved off
# File, mi_edit_ride / mi_clear_ride joined it, and the destructive
# Clear Ride… row sits last (W14's mi_ride_setup stays retired).
RIDE_MENU_ITEMS = (
    "mi_new_ride",
    "mi_edit_ride",
    "mi_start_ride",
    "mi_stop_ride",
    "mi_set_start_time",
    "mi_finish_ride",
    "mi_reopen_ride",
    "mi_audit_trail",
    "mi_clear_ride",
)
# D4: mi_add_entry retired -- the Rider Editor row is the single entry
# point for adding riders. Phase 2 retired mi_entry_detail with its
# window.
RIDERS_MENU_ITEMS = (
    "mi_rider_editor",
    "mi_team_editor",
    "mi_check_rider_issues",
    "mi_mark_dnf",
)
# Phase 2 retired mi_reassign_plate and mi_void_card: Crossing Detail
# now owns both corrections, not the Cards menu. G7 retired
# mi_edit_crossing: Crossing Detail's Edit Time and the F2 accelerator
# cover the same ground. The UI-removals batch retired mi_review_held
# with its row: the console's own review panel is the single surface.
CARDS_MENU_ITEMS = (
    "mi_undo_crossing",
    "mi_add_crossing_at",
    "mi_deal_manual",
)
# Part D: the single Preview in Browser row split per format -- each
# new item gates on its own export existing (HTML / PDF). G6: the five
# checkable publish options follow a separator, after the Preview rows.
# The UI-removals batch authored the two Podium-Poster-HTML rows
# (mi_export_poster_html, mi_preview_poster_html_browser); they are
# routed by the poster-HTML export task (see _PENDING_ROUTE_IDS below).
RESULTS_MENU_ITEMS = (
    "mi_standings",
    "mi_export_html",
    "mi_export_pdf",
    "mi_export_poster",
    "mi_export_poster_html",
    "mi_export_results_csv",
    "mi_preview_html_browser",
    "mi_preview_poster_html_browser",
    "mi_preview_pdf_browser",
    "mi_show_times",
    "mi_laps_board",
    "mi_time_board",
    "mi_full_field",
    "mi_all_cards",
)
ZOOM_MENU_ITEMS = (
    "mi_zoom_90",
    "mi_zoom_100",
    "mi_zoom_110",
    "mi_zoom_120",
    "mi_zoom_130",
    "mi_zoom_140",
    "mi_zoom_150",
)
# W13 (testing notes #14): the theme trio left the View menu -- the
# Settings appearance radios are the single theme surface -- so the
# View row is the two time-column check items plus the seven zoom
# radios.
VIEW_MENU_ITEMS = ("mi_show_total_times", "mi_show_lap_time", *ZOOM_MENU_ITEMS)
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
# button row. The standalone Logo row's wxFilePickerCtrl is retired
# (plan §3d): the Cards box now carries the logo *column* beside
# tiebreak_list -- logo_preview_bmp, logo_status_lbl and
# logo_browse_btn. Phase 5 re-shaped the Cards controls: the
# jokers_choice dropdown became jokers_spin plus the per-deck/total
# radio pair, and cap_chk + cap_spin became one cap_choice dropdown.
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
    "hold_short_radio",
    "always_deal_radio",
    "logo_preview_bmp",
    "logo_status_lbl",
    "logo_browse_btn",
    "solo_radio",
    "mixed_radio",
    "team_size_spin",
    "pooled_radio",
    "relay_radio",
    "decks_spin",
    "jokers_spin",
    "jokers_per_deck_radio",
    "jokers_total_radio",
    "cap_choice",
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

# spec.md section 15's rows after D1/D4, Phase 2, G6, G7 and the
# UI-removals batch (Results lost its tie-break row in Part C and
# split its Preview row per format, then gained G6's five publish
# items; Phase 2 retired mi_entry_detail, mi_reassign_plate and
# mi_void_card, G7 retired mi_edit_crossing, and the removals batch
# retired mi_review_held and authored the two Podium-Poster-HTML
# rows): File 8 (mi_simulation added), Ride 9, Riders 4, Cards 3,
# Results 14, View 1, Help 4. The single
# View row expands into the 9 items section 15b names for it (W13: the
# two time-column check items + the seven zoom radios; the theme trio
# left the View menu).
MENU_ITEM_COUNTS = (
    ("&File", 8),
    ("&Ride", 9),
    ("Ri&ders", 4),
    ("&Cards", 3),
    ("Re&sults", 14),
    ("&View", 9),
    ("&Help", 4),
)

ACCELERATOR_CASES = (
    ("mi_standings", "F5"),
    ("mi_user_guide", "F1"),
    ("mi_undo_crossing", "Ctrl+Z"),
)
ACCELERATED_ITEMS = ("mi_standings", "mi_undo_crossing", "mi_user_guide")

RADIO_MENU_ITEMS = ZOOM_MENU_ITEMS

# Canvas defaults, and the checked default of each of the dialog's four
# radio groups (short-lap policy, entry mode, plate model, jokers mode
# -- Phase 5's jokers_per_deck_radio opens it, jokers_total_radio is
# the checked default).
SELECTED_RADIOS = ("hold_short_radio", "mixed_radio", "pooled_radio", "jokers_total_radio")
GROUP_OPENING_RADIOS = (
    "hold_short_radio",
    "solo_radio",
    "pooled_radio",
    "jokers_per_deck_radio",
)
GROUP_FOLLOWING_RADIOS = (
    "always_deal_radio",
    "mixed_radio",
    "relay_radio",
    "jokers_total_radio",
)

FEED_LIST_NAMES = ("crossings_list", "flagged_list")

# ride_setup_dlg's Cards box after plan §3c/§3d, re-sized in Phase 6:
# tiebreak_list's own three-row box (the view floors it at the same
# 120x120) sits left of the logo column, whose preview bitmap carries
# the 240x240 box.
TIEBREAK_LIST_BOX = "120,120"
LOGO_PREVIEW_BOX = "240,240"
LOGO_COLUMN_NAMES = ("logo_preview_bmp", "logo_status_lbl", "logo_browse_btn")

# The name wxDataViewListCtrl's XRC handler forces onto its control,
# discarding the authored one. It must never appear.
FORCED_DATAVIEW_NAME = "dataviewCtrl"


def _parse(filename: str) -> Element:
    """Return the ``<resource>`` root element of one .xrc file."""
    return parse(XRC_DIR / filename).getroot()


def _top_level_windows(filename: str) -> dict[str, Element]:
    """Map each top-level ``<object name=...>`` to its element."""
    return {child.attrib["name"]: child for child in _parse(filename) if child.tag == "object"}


def _window(window_name: str) -> Element:
    """Return the top-level window element called *window_name*."""
    filename = WINDOWS[window_name][0]
    return _top_level_windows(filename)[window_name]


def _main_frame_top_sizer() -> Element:
    """Return the frame's own outermost sizer (its direct child)."""
    frame = _top_level_windows("main.xrc")["main_frame"]
    return next(child for child in frame if child.attrib.get("class") == "wxBoxSizer")


def _control_names_in(window: Element) -> list[str]:
    """List every named ``<object>`` below *window*, excluding it."""
    return [
        obj.attrib["name"]
        for obj in window.iter("object")
        if "name" in obj.attrib and obj is not window
    ]


def _objects_by_name(window: Element) -> dict[str, Element]:
    """Map every named ``<object>`` in *window* to its element."""
    return {obj.attrib["name"]: obj for obj in window.iter("object") if "name" in obj.attrib}


def _classes_in(filename: str) -> list[str]:
    """List the ``class`` of every ``<object>`` in one .xrc file."""
    return [obj.attrib["class"] for obj in _parse(filename).iter("object")]


def _param(obj: Element, tag: str) -> str:
    """Return the text of *obj*'s direct ``<tag>`` child, or ``""``."""
    child = obj.find(tag)
    return "" if child is None or child.text is None else child.text


def _nearest_sizer(window: Element, name: str) -> Element:
    """Return the innermost sizer that holds the named control.

    Document order puts a control's ancestors before it, so the *last*
    sizer whose subtree contains *name* is its own immediate parent
    sizer -- the nesting fact the Cards-box layout tests pin.
    """
    containing = [
        obj
        for obj in window.iter("object")
        if obj.attrib.get("class", "").endswith("Sizer")
        and any(child.attrib.get("name") == name for child in obj.iter("object"))
    ]
    return containing[-1]


def _sizeritem_of(window: Element, name: str) -> Element:
    """Return the sizer item whose own object is the named control."""
    return next(
        obj
        for obj in window.iter("object")
        if obj.attrib.get("class") == "sizeritem"
        and obj.find("object") is not None
        and obj.find("object").attrib.get("name") == name
    )


def _menus() -> list[Element]:
    """List the ``wxMenu`` children of main_menubar, in order."""
    return [child for child in _window("main_menubar") if child.attrib.get("class") == "wxMenu"]


def _menu_items(menu: Element) -> list[Element]:
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


def test_show_held_only_chk_declares_the_frozen_needs_review_label() -> None:
    """The Needs Review tab's filter box carries its frozen copy."""
    checkbox = next(
        obj
        for obj in _window("main_frame").iter("object")
        if obj.get("name") == "show_held_only_chk"
    )

    assert checkbox.get("class") == "wxCheckBox"
    assert _param(checkbox, "label") == "Show Held Cards Only"


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
    """W9 raised the min 700->780; W15 records the canvas amendment."""
    frame = _top_level_windows("main.xrc")["main_frame"]

    assert _param(frame, "size") == "1100,780"


def test_main_frame_top_sizer_declares_no_spacer_slot() -> None:
    """G4: the retired InfoBar slot is gone from the frame's top sizer.

    The console's vertical sizer used to carry a zero-size ``spacer``
    at index 0 as the insertion point for the two code-side InfoBars,
    so a code-built bar could add a row above the ride-info block.
    Nothing may occupy that slot now.
    """
    classes = [child.attrib["class"] for child in _main_frame_top_sizer() if child.tag == "object"]

    assert "spacer" not in classes


def test_main_frame_top_sizer_leads_with_the_header_row() -> None:
    """G4: the frame's first child is the header, not an empty slot."""
    first = next(child for child in _main_frame_top_sizer() if child.tag == "object")

    assert first.attrib["class"] == "sizeritem"


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


def test_main_menubar_declares_forty_eight_menu_item_names() -> None:
    """The removals batch leaves 48 ``mi_*`` names in main.xrc."""
    names = _control_names_in(_window("main_menubar"))

    menu_item_names = [name for name in names if name.startswith("mi_")]

    assert len(menu_item_names) == 48


# The UI-removals batch authors two Podium-Poster-HTML rows in
# ``main.xrc`` but deliberately does NOT route them: the poster-HTML
# export is a later task, and ``commands.ROUTE_TABLE`` is where its
# "Opens / does" and "Enabled when" cells belong. Until then these ids
# have no route, so they are excluded from the "no orphans" equality
# below -- named here, rather than silently dropped, so the poster-HTML
# task must remove this constant when it registers the routes.
_PENDING_ROUTE_IDS = ("mi_export_poster_html", "mi_preview_poster_html_browser")


def test_main_menubar_item_names_are_exactly_the_routed_item_set() -> None:
    """No orphaned menu item: every authored name is routed, and back.

    ``commands.ROUTE_TABLE`` is the section 15 route map the menubar
    is driven from, so its 49 ids and the authored item names must be
    one set, plus :data:`_PENDING_ROUTE_IDS`' two not-yet-routed
    poster-HTML rows. A row that outlives its route, or a route with no
    item, would leave an item the enablement walk can never reach.
    """
    routed = {item_id for route in commands.ROUTE_TABLE for item_id in route.ids}

    authored = set(_control_names_in(_window("main_menubar")))

    assert routed == authored - set(_PENDING_ROUTE_IDS)
    assert set(_PENDING_ROUTE_IDS) <= authored


def test_file_menu_declares_the_spec_15_row_order_after_d1() -> None:
    """D1: New Ride… left File; the other eight rows remain."""
    file_menu = _menus()[0]

    names = [item.attrib["name"] for item in _menu_items(file_menu)]

    assert tuple(names) == FILE_MENU_ITEMS


def test_ride_menu_declares_the_spec_15_row_order_after_d1() -> None:
    """New/Edit open the menu; the destructive Clear Ride… closes it."""
    ride_menu = _menus()[1]

    names = [item.attrib["name"] for item in _menu_items(ride_menu)]

    assert tuple(names) == RIDE_MENU_ITEMS


def test_results_menu_declares_the_publish_rows_after_the_previews() -> None:
    """G6: the five publish items close the Results menu."""
    results_menu = _menus()[4]

    names = [item.attrib["name"] for item in _menu_items(results_menu)]

    assert tuple(names) == RESULTS_MENU_ITEMS


def test_results_menu_given_the_publish_group_leaves_one_radio_free_run() -> None:
    """G6: the publish items are check items, never radios."""
    results_menu = _menus()[4]

    radios = [
        item.attrib["name"] for item in _menu_items(results_menu) if _param(item, "radio") == "1"
    ]

    assert radios == []


def test_results_menu_given_the_publish_group_separates_it_from_the_previews() -> None:
    """G6: a separator opens the publish group.

    The separator sits directly after the nine export/preview rows
    (the removals batch authored the two Podium-Poster-HTML rows among
    them), so its child index moved with them.
    """
    results_menu = _menus()[4]
    children = [child for child in results_menu if child.tag == "object"]

    assert children[9].attrib["class"] == "separator"


@pytest.mark.parametrize(
    ("item_name", "label"),
    [
        ("mi_export_html", "Export &HTML…"),
        ("mi_show_times", "Show lap && total times"),
        ("mi_laps_board", "Laps leaderboard"),
        ("mi_time_board", "Fastest-time leaderboard"),
        ("mi_full_field", "Full field"),
        ("mi_all_cards", "All cards drawn"),
    ],
)
def test_results_menu_row_declares_its_label(item_name: str, label: str) -> None:
    """G6: the renamed export row, plus the five publish options."""
    item = _objects_by_name(_window("main_menubar"))[item_name]

    assert _param(item, "label") == label


@pytest.mark.parametrize(
    "item_name",
    ["mi_show_times", "mi_laps_board", "mi_time_board", "mi_full_field", "mi_all_cards"],
)
def test_results_publish_row_declares_a_checkable_item(item_name: str) -> None:
    """G6: each publish option is checkable (no radio group)."""
    item = _objects_by_name(_window("main_menubar"))[item_name]

    assert (_param(item, "checkable"), _param(item, "radio")) == ("1", "")


@pytest.mark.parametrize(
    ("item_name", "label"),
    [
        ("mi_new_ride", "&New Ride…"),
        ("mi_edit_ride", "&Edit Ride…"),
        ("mi_clear_ride", "&Clear Ride…"),
    ],
)
def test_new_ride_menu_row_declares_its_label(item_name: str, label: str) -> None:
    """D1: the Ride menu's lifecycle rows carry their frozen verbs."""
    item = _objects_by_name(_window("main_menubar"))[item_name]

    assert _param(item, "label") == label


def test_ride_header_logo_is_declared_as_a_static_bitmap() -> None:
    """C1: the header renders the ride's own logo in its slot."""
    control = _objects_by_name(_window("main_frame"))["ride_logo_bmp"]

    assert control.attrib["class"] == "wxStaticBitmap"


# --------------------------------------------------------------------
# Plan §11: the console header's left-to-right re-layout. The stop
# light's own column leads the ride-info block, the one "Ride" group
# splits into two native boxes -- "Ride" (Name/Date/Venue) and
# "Details" (Organizer/Scorer/Lap length km) -- and the logo slot
# moves out of the group to the right of both, before the clock
# panels. Every box carries <label> text only, so no frozen name and
# no ids.py entry changed.

# The six value rows, split across the two boxes in their authored
# order. Captions are unnamed wxStaticText, like every counter chip's
# caption.
RIDE_GROUP_VALUES = ("ride_name_value", "ride_date_value", "ride_venue_value")
DETAILS_GROUP_VALUES = ("ride_organizer_value", "ride_scorer_value", "ride_lap_km_value")
RIDE_INFO_VALUE_NAMES = (*RIDE_GROUP_VALUES, *DETAILS_GROUP_VALUES)

# The two group boxes, in the order they are laid out.
GROUP_BOX_LABELS = ("Ride", "Details")

# The ride-info block's own children, left to right: the Status group
# (Phase 6 wraps the stop light's column and its label in a titled
# box), then the two group boxes, then the logo slot. A group box is
# summarised as "wxStaticBoxSizer:<label>" -- the label is the only
# thing naming it, which is the point of the §11 no-new-name rule.
HEADER_BLOCK_CHILDREN = (
    "wxStaticBoxSizer:Status",
    "wxStaticBoxSizer:Ride",
    "wxStaticBoxSizer:Details",
    "ride_logo_bmp",
)


def _header_block() -> Element:
    """Return the horizontal row `ride_status_panel` leads (§11).

    Anchored on ``ride_logo_bmp``, a direct child of that row: Phase 6
    wraps ``ride_status_panel`` in the Status box, so the panel's own
    nearest sizer is now that box rather than the row.
    """
    return _nearest_sizer(_window("main_frame"), "ride_logo_bmp")


def _child_summaries(sizer: Element) -> list[str]:
    """Summarise each child: a control's name, or a box's label."""
    children = [item.find("object") for item in sizer if item.attrib.get("class") == "sizeritem"]
    summaries: list[str] = []
    for child in children:
        name = child.attrib.get("name")
        if name:
            summaries.append(name)
        elif child.attrib.get("class") == "wxStaticBoxSizer":
            label = _param(child, "label")
            summaries.append(f"wxStaticBoxSizer:{label}" if label else "wxStaticBoxSizer")
        else:
            summaries.append(child.attrib.get("class", child.tag))
    return summaries


def _group_box(label: str) -> Element:
    """Return main.xrc's ``wxStaticBoxSizer`` carrying *label*."""
    return next(
        obj
        for obj in _window("main_frame").iter("object")
        if obj.attrib.get("class") == "wxStaticBoxSizer" and _param(obj, "label") == label
    )


def _group_grid(label: str) -> Element:
    """Return the *label* box's caption + value ``wxFlexGridSizer``."""
    return next(
        obj
        for obj in _group_box(label).iter("object")
        if obj.attrib.get("class") == "wxFlexGridSizer"
    )


def _group_rows(label: str) -> tuple[tuple[str, str], ...]:
    """Pair each caption cell in *label*'s grid with its value control.

    The grid is row-major -- caption, value, caption, value -- so each
    pair is one "Name -> ride_name_value" row.
    """
    cells = [
        item.find("object")
        for item in _group_grid(label)
        if item.attrib.get("class") == "sizeritem"
    ]
    return tuple(
        (_param(cells[index], "label"), cells[index + 1].attrib["name"])
        for index in range(0, len(cells), 2)
    )


def _group_value_names(label: str) -> list[str]:
    """List the named controls inside the *label* group box."""
    return [obj.attrib["name"] for obj in _group_box(label).iter("object") if "name" in obj.attrib]


def test_ride_header_block_lays_out_status_then_both_groups_then_the_logo() -> None:
    """§11 left to right: stop light, Ride, Details, logo."""
    assert _child_summaries(_header_block()) == list(HEADER_BLOCK_CHILDREN)


def test_ride_status_panel_precedes_both_group_sizers_in_child_order() -> None:
    """§11: the stop-light column leads the ride-info block."""
    children = _child_summaries(_header_block())
    group_indexes = [children.index(f"wxStaticBoxSizer:{label}") for label in GROUP_BOX_LABELS]

    assert children.index("wxStaticBoxSizer:Status") < min(group_indexes)


def test_ride_logo_slot_follows_both_group_sizers_in_child_order() -> None:
    """§11: the logo sits at the far right of the ride-info block."""
    children = _child_summaries(_header_block())

    assert children.index("ride_logo_bmp") > max(
        children.index(f"wxStaticBoxSizer:{label}") for label in GROUP_BOX_LABELS
    )


@pytest.mark.parametrize("label", GROUP_BOX_LABELS)
def test_ride_header_declares_the_two_group_boxes(label: str) -> None:
    """§11: the one Ride group split into "Ride" and "Details"."""
    assert _param(_group_box(label), "label") == label


def test_ride_group_box_holds_the_identity_rows_only() -> None:
    """§11: Name, Date and Venue stay in the "Ride" group."""
    assert _group_value_names("Ride") == list(RIDE_GROUP_VALUES)


def test_details_group_box_holds_the_organizer_rows_only() -> None:
    """§11: Organizer, Scorer and Lap length km move to "Details"."""
    assert _group_value_names("Details") == list(DETAILS_GROUP_VALUES)


# --------------------------------------------------------------------
# Phase 6: the Status box around the stop light's column, and the new
# Current Lap group in the top row.

STATUS_GROUP_LABEL = "Status"
CURRENT_LAP_CAPTION = "Current Lap"


def _containing_sizer(window: Element, inner: Element) -> Element:
    """Return the innermost sizer that directly holds *inner*.

    The reverse of ``_nearest_sizer``: given a sizer, find the one
    wrapping it in a ``sizeritem`` -- how the top row is reached from
    the ride-info block it carries.
    """
    return next(
        obj
        for obj in window.iter("object")
        if obj.attrib.get("class", "").endswith("Sizer")
        and any(
            item.find("object") is inner for item in obj if item.attrib.get("class") == "sizeritem"
        )
    )


def _top_row() -> Element:
    """Return main.xrc's header row, the box above the ride-info block.

    One parse for both lookups: the tree's element objects are only
    identical within a single parse, and ``_containing_sizer`` matches
    the block by identity.
    """
    window = _window("main_frame")
    return _containing_sizer(window, _nearest_sizer(window, "ride_logo_bmp"))


def _lap_group_box() -> Element:
    """Return the Current Lap group box, anchored on its value control.

    The box carries no title (an empty ``<label>``), so ``_group_box``'s
    label lookup cannot find it -- walk up from ``current_lap_lbl``
    instead.
    """
    window = _window("main_frame")
    return _containing_sizer(window, _objects_by_name(window)["current_lap_lbl"])


def test_status_group_box_wraps_the_stop_light_panel_and_its_label() -> None:
    """Part 2: the lamp column and its label share one titled box."""
    names = [
        obj.attrib["name"]
        for obj in _group_box(STATUS_GROUP_LABEL).iter("object")
        if "name" in obj.attrib
    ]

    assert names == ["ride_status_panel", "ride_status_lbl"]


def test_status_group_box_is_the_panels_own_containing_sizer() -> None:
    """The box is the panel's parent sizer, not a sibling of it."""
    panel_sizer = _nearest_sizer(_window("main_frame"), "ride_status_panel")

    assert _param(panel_sizer, "label") == STATUS_GROUP_LABEL


def test_current_lap_group_is_a_static_box_in_the_header_row() -> None:
    """Part 3: the new group takes its own slot in the top row."""
    row_children = _child_summaries(_top_row())

    assert row_children == [
        "wxBoxSizer",
        "wxStaticBoxSizer",
        "wxBoxSizer",
        "wxBoxSizer",
    ]


def test_current_lap_group_holds_the_two_digit_value_and_its_caption() -> None:
    """The value is first, its "Current Lap" caption below it."""
    box = _lap_group_box()
    cells = [
        item.find("object")
        for item in box
        if item.attrib.get("class") == "sizeritem" and item.find("object") is not None
    ]

    assert [(cell.attrib.get("name"), _param(cell, "label")) for cell in cells] == [
        ("current_lap_lbl", "00"),
        (None, CURRENT_LAP_CAPTION),
    ]


def test_current_lap_group_declares_no_box_title() -> None:
    """The box carries no title -- the "Current Lap" caption names it.

    The word ``Lap`` is off the top of the control by request: the
    caption under the reading already says what it is, and the empty
    box label keeps the group from repeating it.
    """
    assert _param(_lap_group_box(), "label") == ""


def test_current_lap_label_declares_the_two_digit_default() -> None:
    """A fresh console reads 00 -- the value before any crossing."""
    control = _objects_by_name(_window("main_frame"))["current_lap_lbl"]

    assert (control.attrib["class"], _param(control, "label")) == ("wxStaticText", "00")


def test_current_lap_label_declares_the_light_green_reading() -> None:
    """Phase 6: light green, authored in XRC.

    ``#90EE90`` is the operator's ``wx.Colour(144, 238, 144)`` -- 90,
    EE and 90 hex.
    """
    control = _objects_by_name(_window("main_frame"))["current_lap_lbl"]

    assert _param(control, "fg") == "#90EE90"


def test_current_lap_label_declares_a_relative_clock_sized_font() -> None:
    """The relative size is what the in-app 90-150% zoom scales from."""
    font = _objects_by_name(_window("main_frame"))["current_lap_lbl"].find("font")

    assert (_param(font, "sysfont"), _param(font, "relativesize"), font.find("size")) == (
        "wxSYS_DEFAULT_GUI_FONT",
        "2",
        None,
    )


def test_current_lap_group_takes_the_column_style_caption_and_value_stack() -> None:
    """A vertical column, like the two clock panels beside it."""
    box = _lap_group_box()

    assert _param(box, "orient") == "wxVERTICAL"


@pytest.mark.parametrize(
    ("label", "rows"),
    [
        (
            "Ride",
            (
                ("Name", "ride_name_value"),
                ("Date", "ride_date_value"),
                ("Venue", "ride_venue_value"),
            ),
        ),
        (
            "Details",
            (
                ("Organizer", "ride_organizer_value"),
                ("Scorer", "ride_scorer_value"),
                ("Lap length km", "ride_lap_km_value"),
            ),
        ),
    ],
    ids=["ride", "details"],
)
def test_group_box_declares_its_captioned_rows_in_order(
    label: str, rows: tuple[tuple[str, str], ...]
) -> None:
    """§11: each box keeps its own three captioned value rows."""
    assert _group_rows(label) == rows


@pytest.mark.parametrize("value_name", RIDE_INFO_VALUE_NAMES)
def test_ride_info_value_is_a_read_only_240_wide_text_ctrl(value_name: str) -> None:
    """§5: each value row is a 240-wide read-only wxTextCtrl."""
    control = _objects_by_name(_window("main_frame"))[value_name]

    assert (control.attrib["class"], _param(control, "style"), _param(control, "size")) == (
        "wxTextCtrl",
        "wxTE_READONLY",
        "240,-1",
    )


def test_ride_logo_slot_declares_the_64_pixel_display_size() -> None:
    """§5: the moved logo slot renders at its own 64x64 size."""
    control = _objects_by_name(_window("main_frame"))["ride_logo_bmp"]

    assert _param(control, "size") == "64,64"


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
    """The seven zoom steps are radio items (the View row's radios)."""
    item = _objects_by_name(_window("main_menubar"))[item_name]

    assert _param(item, "radio") == "1"


def test_view_menu_time_column_items_declare_the_check_kind() -> None:
    """Two check items, which also keep the radio groups apart."""
    items = _objects_by_name(_window("main_menubar"))

    assert (
        _param(items["mi_show_total_times"], "checkable"),
        _param(items["mi_show_lap_time"], "checkable"),
    ) == ("1", "1")


@pytest.mark.parametrize(
    ("item_name", "label"),
    [
        ("mi_show_total_times", "&Show Total Times on Crossings Panel"),
        ("mi_show_lap_time", "&Show Lap Time in Crossings Panel"),
    ],
)
def test_view_menu_time_column_item_declares_its_label(item_name: str, label: str) -> None:
    """Both View rows carry their frozen mnemonic label."""
    item = _objects_by_name(_window("main_menubar"))[item_name]

    assert _param(item, "label") == label


def test_settings_dialog_declares_no_text_zoom_control() -> None:
    """W13: View ▸ Zoom is the single zoom surface (settings.xrc E).

    The settings dialog used to carry ``zoom_choice`` -- the text-zoom
    surface testing notes #12 removed. Its removal is structural here
    (no ``zoom_choice`` object and no ``wxChoice`` at all in the
    dialog); nothing here opens the dialog to check its behaviour.
    """
    names = _control_names_in(_top_level_windows("settings.xrc")["settings_dlg"])
    classes = [obj.attrib["class"] for obj in _parse("settings.xrc").iter("object")]

    assert "zoom_choice" not in names
    assert classes.count("wxChoice") == 0


@pytest.mark.parametrize("radio_name", SELECTED_RADIOS)
def test_canvas_radio_default_declares_value_one(radio_name: str) -> None:
    """hold-short/mixed/pooled start selected, as drawn."""
    radio = _objects_by_name(_window("ride_setup_dlg"))[radio_name]

    assert _param(radio, "value") == "1"


@pytest.mark.parametrize("radio_name", GROUP_OPENING_RADIOS)
def test_radio_group_first_member_declares_rb_group(radio_name: str) -> None:
    """Each of the dialog's three radio groups opens with wxRB_GROUP."""
    radio = _objects_by_name(_window("ride_setup_dlg"))[radio_name]

    assert "wxRB_GROUP" in _param(radio, "style")


@pytest.mark.parametrize("radio_name", GROUP_FOLLOWING_RADIOS)
def test_radio_group_later_member_omits_rb_group(radio_name: str) -> None:
    """A second wxRB_GROUP would split the group it belongs to."""
    radio = _objects_by_name(_window("ride_setup_dlg"))[radio_name]

    assert "wxRB_GROUP" not in _param(radio, "style")


def test_ride_setup_jokers_spin_declares_the_zero_to_ten_range() -> None:
    """Phase 5: the jokers spinner offers 0..10, opening on 1."""
    spin = _objects_by_name(_window("ride_setup_dlg"))["jokers_spin"]

    bounds = (_param(spin, "min"), _param(spin, "max"), _param(spin, "value"))

    assert (spin.attrib["class"], bounds) == ("wxSpinCtrl", ("0", "10", "1"))


def test_ride_setup_jokers_radios_declare_the_two_mode_labels() -> None:
    """jokers_per_deck_radio/jokers_total_radio carry the mode copy."""
    window = _window("ride_setup_dlg")
    per_deck = _objects_by_name(window)["jokers_per_deck_radio"]
    total = _objects_by_name(window)["jokers_total_radio"]

    assert (_param(per_deck, "label"), _param(total, "label")) == ("Per deck", "Total")


def test_ride_setup_cap_choice_declares_disabled_then_five_to_twenty() -> None:
    """Phase 5: one dropdown, "Disabled" first, then 5..20."""
    choice = _objects_by_name(_window("ride_setup_dlg"))["cap_choice"]

    items = [item.text for item in choice.findall("content/item")]

    assert choice.attrib["class"] == "wxChoice"
    assert items[0] == "Disabled"
    assert items[1:] == [str(cap) for cap in range(5, 21)]


def test_ride_setup_cap_choice_opens_on_disabled() -> None:
    """The dropdown's default is no cap (R-13's uncapped default)."""
    choice = _objects_by_name(_window("ride_setup_dlg"))["cap_choice"]

    assert _param(choice, "selection") == "0"


def test_ride_setup_ok_button_declares_the_save_label() -> None:
    """D2: the setup dialog commits with "Save" (both modes).

    The stock id stays ``wxID_OK`` -- wx keeps its platform button
    order and default handling -- only the visible text changes.
    """
    button = _objects_by_name(_window("ride_setup_dlg"))["wxID_OK"]

    assert _param(button, "label") == "Save"


def test_team_size_spin_declares_the_spec_documented_range() -> None:
    """spec.md 1 and 2: 2 to 10 riders per team, default 4."""
    spin = _objects_by_name(_window("ride_setup_dlg"))["team_size_spin"]

    bounds = (_param(spin, "min"), _param(spin, "max"), _param(spin, "value"))

    assert bounds == ("2", "10", "4")


# ------------------------------------------------- W4 lap fields
# (labels declare the entry format, and the short-lap card policy pair
# sits next to them with hold-short as the declared XRC default.)


def test_lap_field_static_labels_declare_the_entry_formats() -> None:
    """The lap labels name their H:MM/M:SS entry shapes."""
    labels = [
        _param(obj, "label")
        for obj in _window("ride_setup_dlg").iter("object")
        if obj.attrib["class"] == "wxStaticText"
    ]

    assert "Duration (H:MM)" in labels
    assert "Min lap (M:SS)" in labels


def test_short_lap_policy_radios_declare_the_two_policy_labels() -> None:
    """hold_short_radio/always_deal_radio carry the W4 policy copy."""
    window = _window("ride_setup_dlg")
    hold = _objects_by_name(window)["hold_short_radio"]
    deal = _objects_by_name(window)["always_deal_radio"]

    assert (_param(hold, "label"), _param(deal, "label")) == (
        "Hold short-lap cards for review",
        "Always deal cards",
    )


# --------------------------------------------------------------------
# Phase 8: the record-crossing row (xrc-windows.md amendments
# A7/P8-D3).


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
    """A7/P8-D3: the "Rider plate" hint and a wider DIP width."""
    control = _objects_by_name(_window("main_frame"))["plate_input"]
    width = int(_param(control, "size").split(",")[0])

    assert (_param(control, "hint"), width >= 200) == ("Rider plate", True)


# --------------------------------------------------------------------
# Plan §3c/§3d: the Cards box's tie-break box and logo column.


def test_ride_setup_tiebreak_list_declares_the_three_row_box() -> None:
    """§3c: a bounded 120x120 box, not a full-width stretch."""
    control = _objects_by_name(_window("ride_setup_dlg"))["tiebreak_list"]

    assert _param(control, "size") == TIEBREAK_LIST_BOX


def test_ride_setup_tiebreak_list_takes_the_rows_own_slack() -> None:
    """The list grows with the Cards box; the logo column does not."""
    item = _sizeritem_of(_window("ride_setup_dlg"), "tiebreak_list")

    assert (_param(item, "option"), _param(item, "flag")) == ("1", "wxEXPAND")


def test_ride_setup_tiebreak_list_sits_beside_the_logo_column() -> None:
    """§3d: tie-break list and logo column share one horizontal row."""
    window = _window("ride_setup_dlg")
    row = _nearest_sizer(window, "tiebreak_list")
    names = [obj.attrib["name"] for obj in row.iter("object") if "name" in obj.attrib]

    assert (_param(row, "orient"), names[0], set(names)) == (
        "wxHORIZONTAL",
        "tiebreak_list",
        {"tiebreak_list", *LOGO_COLUMN_NAMES},
    )


def test_ride_setup_logo_column_stacks_its_three_controls_vertically() -> None:
    """§3d: the preview, the status label and Browse… are one column."""
    column = _nearest_sizer(_window("ride_setup_dlg"), "logo_browse_btn")
    names = [obj.attrib["name"] for obj in column.iter("object") if "name" in obj.attrib]

    assert (_param(column, "orient"), names) == ("wxVERTICAL", list(LOGO_COLUMN_NAMES))


def test_ride_setup_logo_preview_is_declared_as_a_sized_static_bitmap() -> None:
    """§3d: a bitmap-less 240x240 wxStaticBitmap the view fills in."""
    control = _objects_by_name(_window("ride_setup_dlg"))["logo_preview_bmp"]

    assert (control.attrib["class"], _param(control, "size"), control.find("bitmap")) == (
        "wxStaticBitmap",
        LOGO_PREVIEW_BOX,
        None,
    )


def test_ride_setup_logo_status_label_declares_the_no_logo_default() -> None:
    """§3d: a fresh dialog reads "NO LOGO" until one is staged."""
    control = _objects_by_name(_window("ride_setup_dlg"))["logo_status_lbl"]

    assert (control.attrib["class"], _param(control, "label")) == ("wxStaticText", "NO LOGO")


def test_ride_setup_logo_browse_button_declares_the_browse_label() -> None:
    """§3d: the native PNG picker's own button."""
    control = _objects_by_name(_window("ride_setup_dlg"))["logo_browse_btn"]

    assert (control.attrib["class"], _param(control, "label")) == ("wxButton", "Browse…")


def test_ride_setup_declares_no_file_picker_control() -> None:
    """§3d retires the standalone Logo row's wxFilePickerCtrl."""
    classes = [obj.attrib["class"] for obj in _window("ride_setup_dlg").iter("object")]

    assert classes.count("wxFilePickerCtrl") == 0


# --------------------------------------------------------------------
# Plan §9: the crossing-detail Plate prompt (dialogs.xrc).

NUMBER_DLG = "crossing_number_dlg"
# Four digits plus the control's own borders, in DIP: the plate the
# operator retypes is at most four characters (main.xrc's own plate
# hint), so the field declares its width rather than inheriting the
# platform's much wider wxTextCtrl default.
NUMBER_INPUT_SIZE = "50,-1"


def _number_dialog() -> Element:
    """Return dialogs.xrc's ``crossing_number_dlg`` element."""
    return _top_level_windows("dialogs.xrc")[NUMBER_DLG]


def test_crossing_number_dlg_is_declared_as_a_top_level_wx_dialog() -> None:
    """LoadDialog resolves the prompt by its frozen window name."""
    assert _number_dialog().attrib["class"] == "wxDialog"


def test_crossing_number_dlg_declares_the_plate_caption() -> None:
    """UX-DESKTOP §7: the one input carries a real, persistent label."""
    labels = [
        _param(obj, "label")
        for obj in _number_dialog().iter("object")
        if obj.attrib["class"] == "wxStaticText"
    ]

    assert labels == ["Plate"]


def test_crossing_number_dlg_number_input_is_a_four_digit_text_ctrl() -> None:
    """The prompt's one editable control is the four-digit field."""
    control = _objects_by_name(_number_dialog())["number_input"]

    assert (control.attrib["class"], _param(control, "size")) == (
        "wxTextCtrl",
        NUMBER_INPUT_SIZE,
    )


def test_crossing_number_dlg_declares_the_save_and_cancel_stock_buttons() -> None:
    """§9: Save is the default; Cancel keeps Escape's route."""
    buttons = [
        (obj.attrib["name"], _param(obj, "label"), _param(obj, "default"))
        for obj in _number_dialog().iter("object")
        if obj.attrib["class"] == "wxButton"
    ]

    assert buttons == [("wxID_OK", "Save", "1"), ("wxID_CANCEL", "Cancel", "")]


def test_crossing_number_dlg_declares_exactly_one_std_dialog_button_sizer() -> None:
    """UX-DESKTOP §3: the stock sizer owns button order."""
    classes = [obj.attrib["class"] for obj in _number_dialog().iter("object")]

    assert classes.count("wxStdDialogButtonSizer") == 1


def test_crossing_number_dlg_declares_no_duplicate_control_name() -> None:
    """Section 15b: names are unique within their window."""
    counts = Counter(_control_names_in(_number_dialog()))

    repeated = sorted(name for name, count in counts.items() if count > 1)

    assert repeated == []


# --------------------------------------------------------------------
# Plan §1: the Rider Simulator's two Generate buttons collapse into the
# one "Generate Riders" button, so gen_teams_btn leaves the XRC file
# (and, with it, ui/ids.py). Phase 2 adds check_btn beside it and
# re-authors the count defaults. G9 adds the three behaviour dropdowns
# under the interval row.

SIMULATION_XRC = "simulation.xrc"
SIMULATION_DLG = "simulation_dlg"
SIMULATION_DIALOG_CONTROLS = (
    "riders_spin",
    "teams_spin",
    "solo_spin",
    "laps_spin",
    "interval_spin",
    "short_lap_choice",
    "lapped_choice",
    "team_stop_choice",
    "gen_riders_btn",
    "check_btn",
    "go_btn",
    "wxID_CANCEL",
)


def _simulation_dialog() -> Element:
    """Return simulation.xrc's ``simulation_dlg`` element."""
    return _top_level_windows(SIMULATION_XRC)[SIMULATION_DLG]


def test_simulation_dlg_declares_one_generate_button_and_no_gen_teams_btn() -> None:
    """Plan §1: gen_riders_btn is the only Generate button now."""
    names = _control_names_in(_simulation_dialog())

    assert sorted(names) == sorted(SIMULATION_DIALOG_CONTROLS)
    assert "gen_teams_btn" not in names


def test_simulation_dlg_declares_the_check_button_in_the_generator_row() -> None:
    """Plan §2: "Check" sits beside Generate Riders."""
    dialog = _simulation_dialog()
    objects = _objects_by_name(dialog)
    row = _nearest_sizer_of(dialog, objects["gen_riders_btn"])

    assert any(item is objects["check_btn"] for item in row.iter("object"))
    assert _param(objects["check_btn"], "label") == "Check"


def test_simulation_dlg_declares_the_new_count_defaults() -> None:
    """Plan §1/§3: the authored defaults are 175 / 40 / 15 / 1 / 45."""
    objects = _objects_by_name(_simulation_dialog())

    assert (
        _param(objects["riders_spin"], "value"),
        _param(objects["teams_spin"], "value"),
        _param(objects["solo_spin"], "value"),
        _param(objects["laps_spin"], "value"),
        _param(objects["interval_spin"], "value"),
    ) == ("175", "40", "15", "1", "45")


# --------------------------------------------------------------------
# Plan §10: the card-sufficiency line in rider_issues_dlg and the
# average rider speed entry in settings_dlg. Phase 1 re-shaped the
# latter (rounded integer spin -> one-decimal entry with the frozen
# "Avg Lap Time (kmh)" label) and retired the Back up now button.


def _settings_dialog() -> Element:
    """Return settings.xrc's ``settings_dlg`` element."""
    return _top_level_windows("settings.xrc")["settings_dlg"]


def _rider_issues_dialog() -> Element:
    """Return riders.xrc's ``rider_issues_dlg`` element."""
    return _top_level_windows("riders.xrc")["rider_issues_dlg"]


def _nearest_sizer_of(window: Element, target: Element) -> Element:
    """Return the innermost sizer whose own subtree holds *target*.

    The element-identity twin of :func:`_nearest_sizer`: it locates
    the sizer that owns an anonymous control (the entry's caption)
    rather than one addressed by name.
    """
    containing = [
        obj
        for obj in window.iter("object")
        if obj.attrib.get("class", "").endswith("Sizer")
        and any(child is target for child in obj.iter("object"))
    ]
    return containing[-1]


def test_settings_dlg_declares_the_average_speed_decimal_entry() -> None:
    """Plan §10: a 12.0 authoring default, 1 decimal, floored at 1."""
    entry = _objects_by_name(_settings_dialog())["avg_speed_spin"]

    assert (
        entry.attrib["class"],
        _param(entry, "value"),
        _param(entry, "digits"),
        _param(entry, "min"),
        _param(entry, "size"),
    ) == ("wxSpinCtrlDouble", "12.0", "1", "1", "60,-1")


SETTINGS_TIME_CHECKBOXES = (
    ("show_total_times_chk", "Show Total Times on Crossings Panel"),
    ("show_lap_time_chk", "Show Lap Time in Crossings Panel"),
)


@pytest.mark.parametrize(("name", "label"), SETTINGS_TIME_CHECKBOXES)
def test_settings_dlg_declares_the_time_column_checkbox_label(name: str, label: str) -> None:
    """Both checkboxes carry their frozen copy."""
    checkbox = _objects_by_name(_settings_dialog())[name]

    assert (checkbox.attrib["class"], _param(checkbox, "label")) == ("wxCheckBox", label)


@pytest.mark.parametrize(("name", "_label"), SETTINGS_TIME_CHECKBOXES)
def test_settings_dlg_time_column_checkbox_declares_no_checked_state(
    name: str, _label: str
) -> None:
    """A stored setting: the presenter seeds it, so no ``<checked>``."""
    checkbox = _objects_by_name(_settings_dialog())[name]

    assert checkbox.find("checked") is None


def test_settings_avg_speed_entry_declares_its_avg_lap_time_label() -> None:
    """The entry's caption is the frozen "Avg Lap Time (kmh)" string."""
    labels = [
        _param(obj, "label")
        for obj in _settings_dialog().iter("object")
        if obj.find("label") is not None
    ]

    assert "Avg Lap Time (kmh)" in labels


def test_settings_avg_speed_label_owns_the_entrys_own_row() -> None:
    """The caption and the entry share one box: they move together.

    A persistent label control, not a placeholder on the entry
    (UX-DESKTOP section 7): the caption's own sizer is the entry's.
    """
    dialog = _settings_dialog()
    label = next(
        obj for obj in dialog.iter("object") if _param(obj, "label") == "Avg Lap Time (kmh)"
    )
    entry = _objects_by_name(dialog)["avg_speed_spin"]

    assert _nearest_sizer_of(dialog, label) is _nearest_sizer_of(dialog, entry)


def test_settings_dlg_declares_no_backup_now_button() -> None:
    """Phase 1: the dialog's manual-backup button is gone.

    File ▸ Back Up Database… (mi_backup_now) remains the single R-54
    manual-backup surface.
    """
    names = _control_names_in(_settings_dialog())

    assert "backup_now_btn" not in names


def test_rider_issues_dlg_declares_the_card_check_label() -> None:
    """Plan §10: a static-text line, empty until the view renders it."""
    label = _objects_by_name(_rider_issues_dialog())["card_check_lbl"]

    assert (label.attrib["class"], _param(label, "label")) == ("wxStaticText", "")


def test_rider_issues_card_check_label_precedes_the_issue_summary() -> None:
    """The verdict reads above the issue-count summary."""
    names = _control_names_in(_rider_issues_dialog())

    assert names.index("card_check_lbl") < names.index("issues_summary_lbl")
