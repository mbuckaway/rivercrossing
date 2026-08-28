# SPDX-License-Identifier: GPL-3.0-only
"""E5.4.2: capture the empty-state screens after demo retirement.

With no store-backed ride open, every window the app can reach renders
its correct EMPTY state instead of demo rows (results: no standings;
library: no rides; rider editor: no riders; entry detail: an empty
entry; console: a fresh engine feed). Each test drives one window with
the same empty-state source the app bootstrap now wires
(``app._build_console_engine`` over an empty roster for the console,
``EmptyDataSource`` for the E6/E7 windows, an empty mixed roster for
the editor) and saves a screenshot under ``tests/functional/
_screenshots/`` -- the repo's convention (``test_screen_smoke.py``),
pulled back from the Tart VM by ``scripts/run_functional_tests_vm.sh``.

The row-count assertions make each screenshot a genuine proof of the
empty state, not a side effect: a screenshot of a populated window
would fail the assertion that drove it.
"""

from pathlib import Path

import harness
import pytest

from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui import ids
from rivercrossing.ui.presenters.data_source import EmptyDataSource
from rivercrossing.ui.views import MainFrame
from rivercrossing.ui.views.entry_detail import EntryDetailDialog
from rivercrossing.ui.views.results_win import ResultsWindow
from rivercrossing.ui.views.ride_library import RideLibrary
from rivercrossing.ui.views.rider_editor import RiderEditor

pytestmark = pytest.mark.functional

SCREENSHOT_DIR = Path(__file__).resolve().parent / "_screenshots"


def _empty_mixed_roster() -> Roster:
    """Return the bootstrap's empty mixed/pooled roster (E5.4.2)."""
    return Roster(
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
        max_team_size=4,
    )


def test_main_frame_empty_state_screenshot_captures_the_empty_feed(
    xrc_resource: object,
) -> None:
    """The console's real empty state: fresh engine, zero crossings."""
    window = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        _engine, source = app_module._build_console_engine(_empty_mixed_roster())
        console = MainFrame(window, data_source=source, resource=xrc_resource)
        saved = harness.screenshot(window, SCREENSHOT_DIR / "main_frame_empty_state.png")
        row_count = console.crossings_list.GetModel().GetCount()
    finally:
        # Keep the view referenced until the window dies (Phase 2
        # reference hygiene + the model wrapper the count reads).
        del console
        harness.close_window(window)

    assert row_count == 0
    assert saved.exists()


def test_results_frame_empty_state_screenshot_captures_no_standings(
    xrc_resource: object,
) -> None:
    """Results render zero standings rows until E6 wires real data."""
    window = harness.load_window_verified(xrc_resource, ids.RESULTS_FRAME, frame=True)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        view = ResultsWindow(window, data_source=EmptyDataSource())
        saved = harness.screenshot(window, SCREENSHOT_DIR / "results_frame_empty_state.png")
        row_count = view.standings_list.GetModel().GetCount()
    finally:
        del view
        harness.close_window(window)

    assert row_count == 0
    assert saved.exists()


def test_ride_library_dlg_empty_state_screenshot_captures_no_rides(
    xrc_resource: object,
) -> None:
    """The no-store library renders zero rides until one is created."""
    window = harness.load_window_verified(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        view = RideLibrary(window, data_source=EmptyDataSource())
        saved = harness.screenshot(window, SCREENSHOT_DIR / "ride_library_dlg_empty_state.png")
        row_count = view.rides_list.GetModel().GetCount()
    finally:
        del view
        harness.close_window(window)

    assert row_count == 0
    assert saved.exists()


def test_rider_editor_dlg_empty_state_screenshot_captures_no_riders(
    xrc_resource: object,
) -> None:
    """The bootstrap rider editor renders zero riders (empty roster)."""
    window = harness.load_window_verified(xrc_resource, ids.RIDER_EDITOR_DLG, frame=False)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        view = RiderEditor(window, roster=_empty_mixed_roster())
        saved = harness.screenshot(window, SCREENSHOT_DIR / "rider_editor_dlg_empty_state.png")
        row_count = view.riders_list.GetModel().GetCount()
    finally:
        del view
        harness.close_window(window)

    assert row_count == 0
    assert saved.exists()


def test_entry_detail_dlg_empty_state_screenshot_captures_the_empty_entry(
    xrc_resource: object,
) -> None:
    """Entry detail opens empty until E7 wires the real lookup."""
    window = harness.load_window_verified(xrc_resource, ids.ENTRY_DETAIL_DLG, frame=False)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        view = EntryDetailDialog(window, "", data_source=EmptyDataSource())
        saved = harness.screenshot(window, SCREENSHOT_DIR / "entry_detail_dlg_empty_state.png")
        header = view.entry_header_lbl.GetLabelText()
        laps_count = view.laps_list.GetModel().GetCount()
        cards_count = view.cards_list.GetModel().GetCount()
    finally:
        del view
        harness.close_window(window)

    assert (header, laps_count, cards_count) == ("", 0, 0)
    assert saved.exists()
