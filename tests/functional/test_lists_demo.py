# SPDX-License-Identifier: GPL-3.0-only
"""Real-toolkit tests for the list windows' demo display (E1.5.2).

``RideLibrary`` and ``RiderEditor`` each decorate an already-XRC-
loaded window (``harness.load_window``, the same pattern
``test_console_demo.py`` uses for ``MainFrame``) with the code-side
bindings xrc-windows.md's per-window footnotes assign to it: each
DataView's columns and rows and ``rider_editor_dlg``'s solo-only Team
column.

The ``entry_detail_dlg`` and ``results_frame`` suites moved to
``test_lists_entry_detail.py`` and ``test_lists_results.py`` with
their own module-scoped fixtures: splitting the two heaviest
functional files spreads per-worker window churn across
``--dist loadfile`` workers (the wrapper-cache corruption remedy).
The constants and helpers all three files share live in
``_lists_common``.

None of the four windows here carries a splitter, so the one
rebuild-and-compare hazard this suite's harness warns about
(mutate a sash, destroy, rebuild) does not apply -- every window
below is built exactly once per test module and never torn down and
rebuilt mid-module, so plain module-scoped fixtures are enough
(module docstring of ``test_console_demo.py`` explains the hazard
this deliberately avoids triggering).
"""

import re

import harness
import pytest
from _lists_common import (
    CANVAS_RIDERS,
    CANVAS_RIDES,
    MAX_SCREEN_HEIGHT,
    MAX_SCREEN_WIDTH,
    _model_row,
    _spy_repaint,
)

from rivercrossing.demo import DemoDataSource
from rivercrossing.ride import RideStatus
from rivercrossing.roster import Roster
from rivercrossing.ui import ids
from rivercrossing.ui.app import _seed_roster
from rivercrossing.ui.presenters.data_source import RideSummary
from rivercrossing.ui.views import ride_library, rider_editor
from rivercrossing.ui.views.ride_library import RideLibrary
from rivercrossing.ui.views.rider_editor import COL_TEAM, RiderEditor

pytestmark = pytest.mark.functional


# ----------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def shared_library(xrc_resource: object) -> RideLibrary:
    """One ``RideLibrary``, reused by every read-only assertion."""
    window = harness.load_window_verified(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        view = RideLibrary(window, data_source=DemoDataSource())
        yield view
    finally:
        # Phase 2 reference hygiene: drop the view before the window
        # dies (see test_console_demo.py's shared_console finally).
        del view
        harness.close_window(window)


@pytest.fixture(scope="module")
def shared_rider_editor(xrc_resource: object) -> RiderEditor:
    """One ``RiderEditor``, built against the demo's mixed roster."""
    window = harness.load_window_verified(xrc_resource, ids.RIDER_EDITOR_DLG, frame=False)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        view = RiderEditor(window, roster=_seed_roster(DemoDataSource()))
        yield view
    finally:
        # Phase 2 reference hygiene: drop the view before the window
        # dies (see test_console_demo.py's shared_console finally).
        del view
        harness.close_window(window)


# --------------------------------------------------- ride_library_dlg


def test_ride_library_shows_two_rides_matching_the_canvas_exactly(
    shared_library: RideLibrary,
) -> None:
    """xrc-windows.md D: GORBA EPIC 2026 (RUNNING) then poker night."""
    model = shared_library.rides_list.GetModel()

    rows = tuple(_model_row(model, row, range(4)) for row in range(model.GetCount()))

    assert rows == CANVAS_RIDES


def test_ride_library_given_a_different_source_shows_its_rows_not_the_demo(
    xrc_resource: object,
) -> None:
    """Req 6: a no-op binding would keep showing demo rows, not this."""

    class _StubSource:
        def rides(self) -> list[RideSummary]:
            return [
                RideSummary(
                    name="Stub Ride", date="2099-01-01", status=RideStatus.DRAFT, entries=1
                )
            ]

    window = harness.load_window_verified(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    try:
        window.Show()
        harness.pump()
        view = RideLibrary(window, data_source=_StubSource())
        model = view.rides_list.GetModel()
        row = _model_row(model, 0, range(4))
    finally:
        harness.close_window(window)

    assert row == ("Stub Ride", "2099-01-01", "DRAFT", "1")


def test_ride_library_applies_the_canvas_minimum_width(shared_library: RideLibrary) -> None:
    """D16: the canvas draws this dialog at 520px."""
    size = shared_library.dialog.GetSize()

    assert size.width == ride_library.MIN_SIZE[0]


def test_ride_library_fits_within_1366x768(shared_library: RideLibrary) -> None:
    """UX-DESKTOP §6: every window must fit the field-laptop floor."""
    size = shared_library.dialog.GetSize()

    assert size.width <= MAX_SCREEN_WIDTH
    assert size.height <= MAX_SCREEN_HEIGHT


# --------------------------------------------------- rider_editor_dlg


def test_rider_editor_shows_four_riders_matching_the_canvas_exactly(
    shared_rider_editor: RiderEditor,
) -> None:
    """xrc-windows.md C: Ellis, Roy/Singh (Trail Blazers), Chen."""
    model = shared_rider_editor.riders_list.GetModel()

    rows = tuple(_model_row(model, row, range(3)) for row in range(model.GetCount()))

    assert rows == CANVAS_RIDERS


def test_rider_editor_team_column_is_shown_for_the_demos_mixed_roster(
    shared_rider_editor: RiderEditor,
) -> None:
    """Req 3: mixed solo+team riders keep the Team column visible."""
    column = shared_rider_editor.riders_list.GetColumn(COL_TEAM)

    assert column.IsHidden() is False


def test_rider_editor_team_column_is_hidden_for_a_solo_only_roster(
    xrc_resource: object,
) -> None:
    """Req 3: a solo-only roster hides the Team column entirely."""
    roster = Roster()
    roster.create_solo_entry(name="Solo One", plate="1")
    roster.create_solo_entry(name="Solo Two", plate="2")

    window = harness.load_window_verified(xrc_resource, ids.RIDER_EDITOR_DLG, frame=False)
    try:
        window.Show()
        harness.pump()
        view = RiderEditor(window, roster=roster)
        hidden = view.riders_list.GetColumn(COL_TEAM).IsHidden()
    finally:
        harness.close_window(window)

    assert hidden is True


def test_rider_editor_applies_the_canvas_minimum_width(shared_rider_editor: RiderEditor) -> None:
    """D16: the canvas draws this dialog at 640px."""
    size = shared_rider_editor.dialog.GetSize()

    assert size.width == rider_editor.MIN_SIZE[0]


def test_rider_editor_fits_within_1366x768(shared_rider_editor: RiderEditor) -> None:
    """UX-DESKTOP §6: every window must fit the field-laptop floor."""
    size = shared_rider_editor.dialog.GetSize()

    assert size.width <= MAX_SCREEN_WIDTH
    assert size.height <= MAX_SCREEN_HEIGHT


# ------------------------------------------------- negative path: _find


def test_ride_library_find_given_an_unknown_control_name_raises_naming_it(
    shared_library: RideLibrary,
) -> None:
    """T-5: the one ``raise`` in ``views/ride_library.py``."""
    with pytest.raises(LookupError, match=re.escape("no control named 'no_such_control'")):
        shared_library._find("no_such_control")


def test_rider_editor_find_given_an_unknown_control_name_raises_naming_it(
    shared_rider_editor: RiderEditor,
) -> None:
    """T-5: the one ``raise`` in ``views/rider_editor.py``."""
    with pytest.raises(LookupError, match=re.escape("no control named 'no_such_control'")):
        shared_rider_editor._find("no_such_control")


# ------------------------------ repaint after model (unverified remedy)


def test_ride_library_show_rides_repaints_the_list_after_associating_its_model(
    xrc_resource: object,
) -> None:
    """Unverified remedy; see ``associate_model``'s docstring."""
    window = harness.load_window_verified(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    try:
        window.Show()
        harness.pump()
        # control kept alive: _spy_repaint's docstring.
        control = harness.find_control(window, ids.RIDES_LIST)
        refresh, update = _spy_repaint(control)
        view = RideLibrary(window, data_source=DemoDataSource())
        row_count = view.rides_list.GetModel().GetCount()
    finally:
        harness.close_window(window)

    assert row_count == len(CANVAS_RIDES)
    refresh.assert_called_once_with()
    update.assert_called_once_with()


def test_rider_editor_show_riders_repaints_the_list_after_associating_its_model(
    xrc_resource: object,
) -> None:
    """Unverified remedy; see ``associate_model``'s docstring."""
    window = harness.load_window_verified(xrc_resource, ids.RIDER_EDITOR_DLG, frame=False)
    try:
        window.Show()
        harness.pump()
        # control kept alive: _spy_repaint's docstring.
        control = harness.find_control(window, ids.RIDERS_LIST)
        refresh, update = _spy_repaint(control)
        view = RiderEditor(window, roster=_seed_roster(DemoDataSource()))
        row_count = view.riders_list.GetModel().GetCount()
    finally:
        harness.close_window(window)

    assert row_count == len(CANVAS_RIDERS)
    refresh.assert_called_once_with()
    update.assert_called_once_with()
