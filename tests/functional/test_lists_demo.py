# SPDX-License-Identifier: GPL-3.0-only
"""Real-toolkit tests for the four list windows' demo display (E1.5.2).

``RideLibrary``/``RiderEditor``/``EntryDetailDialog``/``ResultsWindow``
each decorate an already-XRC-loaded window (``harness.load_window``,
the same pattern ``test_console_demo.py`` uses for ``MainFrame``)
with the code-side bindings xrc-windows.md's per-window footnotes
assign to it: each DataView's columns and rows, ``rider_editor_dlg``'s
solo-only Team column, and ``entry_detail_dlg``'s two card bitmap
columns.

None of the four windows here carries a splitter, so the one
rebuild-and-compare hazard this suite's harness warns about
(mutate a sash, destroy, rebuild) does not apply -- every window
below is built exactly once per test module and never torn down and
rebuilt mid-module, so plain module-scoped fixtures are enough
(module docstring of ``test_console_demo.py`` explains the hazard
this deliberately avoids triggering).
"""

import re
from typing import Any
from unittest.mock import MagicMock

import harness
import pytest

from rivercrossing.demo import DemoDataSource
from rivercrossing.ride import RideStatus
from rivercrossing.roster import Roster
from rivercrossing.ui import ids
from rivercrossing.ui.app import _seed_roster
from rivercrossing.ui.presenters.data_source import RideSummary, StandingsRow
from rivercrossing.ui.views import _support, entry_detail, results_win, ride_library, rider_editor
from rivercrossing.ui.views.entry_detail import COL_CARD as LAPS_COL_CARD
from rivercrossing.ui.views.entry_detail import EntryDetailDialog
from rivercrossing.ui.views.results_win import ResultsWindow
from rivercrossing.ui.views.ride_library import RideLibrary
from rivercrossing.ui.views.rider_editor import COL_TEAM, RiderEditor

pytestmark = pytest.mark.functional

MAX_SCREEN_WIDTH = 1366
MAX_SCREEN_HEIGHT = 768

# --- xrc-windows.md's own tables, transcribed independently of demo.py
# so a transcription mistake in either place is caught by the other
# disagreeing, not by this test checking demo.py against itself. ---

CANVAS_RIDES = (
    ("GORBA EPIC 2026", "2026-09-20", "RUNNING", "180"),
    ("Club poker night", "2026-06-11", "FINISHED", "24"),
)

CANVAS_RIDERS = (
    ("123", "Sam Ellis", "—"),
    ("77", "A. Roy", "Trail Blazers"),
    ("78", "K. Singh", "Trail Blazers"),
    ("212", "M. Chen", "—"),
)

CANVAS_ENTRY_HEADER = "Team · 3 riders · 9 laps · 3:02:11"
CANVAS_ENTRY_MEMBERS = "A. Roy (77) · K. Singh (78) · L. Marchetti (79)"
CANVAS_LAPS = (
    ("9", "14:22:18", "19:55", "78"),
    ("8", "14:02:23", "21:40", "77"),
)
CANVAS_LAPS_CARD_KEYS = ("Kc", "joker")  # KC -> Kc, JK -> joker (asset_key)
CANVAS_CARDS_HELD_KEYS = ("9h", "Ks", "Kc", "joker", "4d")  # demo.py's own 5-of-9 fixture

CANVAS_STANDINGS = (
    ("1", "77", "Trail Blazers", "9", "5:44:02", "K♠ K♣ K♦ JK★ 9♥", "Four of a kind, kings"),
    ("2", "123", "Sam Ellis", "8", "5:51:17", "Q♥ J♥ T♥ 9♥ 8♥", "Straight flush, queen-high"),
    ("3", "8", "R. Dubois", "7", "5:38:44", "A♣ A♦ A♥ 4♦ 4♠", "Full house, aces over fours"),
)

CANVAS_PUBLISH_DEFAULTS = (
    (ids.SHOW_TIMES_CHK, False),
    (ids.LAPS_BOARD_CHK, True),
    (ids.TIME_BOARD_CHK, False),
    (ids.FULL_FIELD_CHK, True),
    (ids.ALL_CARDS_CHK, True),
)


# ----------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def shared_library(xrc_resource: object) -> RideLibrary:
    """One ``RideLibrary``, reused by every read-only assertion."""
    window = harness.load_window(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        view = RideLibrary(window, data_source=DemoDataSource())
        yield view
    finally:
        harness.close_window(window)


@pytest.fixture(scope="module")
def shared_rider_editor(xrc_resource: object) -> RiderEditor:
    """One ``RiderEditor``, built against the demo's mixed roster."""
    window = harness.load_window(xrc_resource, ids.RIDER_EDITOR_DLG, frame=False)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        view = RiderEditor(window, roster=_seed_roster(DemoDataSource()))
        yield view
    finally:
        harness.close_window(window)


@pytest.fixture(scope="module")
def shared_entry_detail(xrc_resource: object) -> EntryDetailDialog:
    """One ``EntryDetailDialog``, built for the demo's plate 77."""
    window = harness.load_window(xrc_resource, ids.ENTRY_DETAIL_DLG, frame=False)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        view = EntryDetailDialog(window, "77", data_source=DemoDataSource())
        yield view
    finally:
        harness.close_window(window)


@pytest.fixture(scope="module")
def shared_results(xrc_resource: object) -> ResultsWindow:
    """One ``ResultsWindow``, reused by every read-only assertion."""
    window = harness.load_window(xrc_resource, ids.RESULTS_FRAME, frame=True)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        view = ResultsWindow(window, data_source=DemoDataSource())
        yield view
    finally:
        harness.close_window(window)


def _model_row(model: Any, row: int, columns: range) -> tuple[str, ...]:  # noqa: ANN401
    """Return every text cell of *row*, in column order."""
    return tuple(model.GetValueByRow(row, col) for col in columns)


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

    window = harness.load_window(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
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

    window = harness.load_window(xrc_resource, ids.RIDER_EDITOR_DLG, frame=False)
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


# --------------------------------------------------- entry_detail_dlg


def test_entry_detail_shows_the_canvas_header_and_members_for_plate_77(
    shared_entry_detail: EntryDetailDialog,
) -> None:
    """xrc-windows.md C: header + member roster text for plate 77."""
    header = shared_entry_detail.entry_header_lbl.GetLabelText()
    members = shared_entry_detail.members_lbl.GetLabelText()

    assert (header, members) == (CANVAS_ENTRY_HEADER, CANVAS_ENTRY_MEMBERS)


def test_entry_detail_laps_list_shows_two_rows_matching_the_canvas_exactly(
    shared_entry_detail: EntryDetailDialog,
) -> None:
    """xrc-windows.md C: lap 9 (rider 78) then lap 8 (rider 77)."""
    model = shared_entry_detail.laps_list.GetModel()

    rows = tuple(_model_row(model, row, range(4)) for row in range(model.GetCount()))

    assert rows == CANVAS_LAPS


def test_entry_detail_laps_list_card_column_renders_the_dealt_bitmaps(
    shared_entry_detail: EntryDetailDialog,
) -> None:
    """Req 4: a real imagelist bitmap, not just a typed column."""
    model = shared_entry_detail.laps_list.GetModel()

    rendered = tuple(model.GetValueByRow(row, LAPS_COL_CARD) for row in range(model.GetCount()))

    assert rendered == tuple(
        shared_entry_detail.card_images.bitmap(key) for key in CANVAS_LAPS_CARD_KEYS
    )


def test_entry_detail_cards_list_renders_the_held_card_bitmaps(
    shared_entry_detail: EntryDetailDialog,
) -> None:
    """Req 4: cards_list's one column is a real bitmap per held card."""
    model = shared_entry_detail.cards_list.GetModel()

    rendered = tuple(model.GetValueByRow(row, 0) for row in range(model.GetCount()))

    assert rendered == tuple(
        shared_entry_detail.card_images.bitmap(key) for key in CANVAS_CARDS_HELD_KEYS
    )


def test_entry_detail_card_images_defaults_to_the_shared_support_cache(
    shared_entry_detail: EntryDetailDialog,
) -> None:
    """The extracted ``_support.default_card_images`` backs this deck.

    Also proves the merge's real effect: before extraction,
    ``main_frame.py`` and ``entry_detail.py`` each cached their own
    separate 53-bitmap deck; ``test_console_demo.py``'s equivalent
    assertion for ``shared_console`` shares this exact object.
    """
    assert shared_entry_detail.card_images is _support.default_card_images()


def test_entry_detail_given_an_unknown_plate_raises_naming_it(xrc_resource: object) -> None:
    """T-5: ``DemoDataSource.entry_detail``'s only ``raise``."""
    window = harness.load_window(xrc_resource, ids.ENTRY_DETAIL_DLG, frame=False)
    try:
        window.Show()
        harness.pump()
        expected = re.escape("no entry detail for plate 'no-such-plate'")
        with pytest.raises(LookupError, match=expected):
            EntryDetailDialog(window, "no-such-plate", data_source=DemoDataSource())
    finally:
        harness.close_window(window)


def test_entry_detail_fits_within_1366x768(shared_entry_detail: EntryDetailDialog) -> None:
    """UX-DESKTOP §6: every window must fit the field-laptop floor."""
    size = shared_entry_detail.dialog.GetSize()

    assert size.width <= MAX_SCREEN_WIDTH
    assert size.height <= MAX_SCREEN_HEIGHT


def test_entry_detail_applies_the_measured_minimum_width(
    shared_entry_detail: EntryDetailDialog,
) -> None:
    """D16: no canvas width is stated; the floor is now forced.

    Measured on windows-latest CI (run 31015653629): Fit() alone
    landed on 650px there, Segoe UI's button metrics fitting
    narrower than macOS's Fit()-derived 726px. The width is now
    forced before Fit(), exactly like ride_library/rider_editor/
    results_win already force their canvas widths (D16). This
    assertion's red state is only visible on windows-latest: on
    macOS, Fit() alone already lands on entry_detail.MIN_SIZE[0],
    so nothing appears to change locally -- the accepted repo
    precedent for a platform-only fix.
    """
    size = shared_entry_detail.dialog.GetSize()

    assert size.width == entry_detail.MIN_SIZE[0]


# ------------------------------------------------------ results_frame


def test_results_window_shows_three_standings_matching_the_canvas_exactly(
    shared_results: ResultsWindow,
) -> None:
    """xrc-windows.md D: 77/Trail Blazers, 123/Ellis, 8/Dubois."""
    model = shared_results.standings_list.GetModel()

    rows = tuple(_model_row(model, row, range(7)) for row in range(model.GetCount()))

    assert rows == CANVAS_STANDINGS


def test_results_window_given_a_different_source_shows_its_rows_not_the_demo(
    xrc_resource: object,
) -> None:
    """Req 6: a no-op binding would keep showing demo rows, not this."""

    class _StubSource:
        def standings(self) -> list[StandingsRow]:
            return [
                StandingsRow(
                    place=1,
                    plate="999",
                    entry="Stub Entry",
                    laps=1,
                    total="0:00:01",
                    best5=("2C", "2D", "2H", "2S", "3C"),
                    hand="Stub hand",
                )
            ]

    window = harness.load_window(xrc_resource, ids.RESULTS_FRAME, frame=True)
    try:
        window.Show()
        harness.pump()
        view = ResultsWindow(window, data_source=_StubSource())
        model = view.standings_list.GetModel()
        row = _model_row(model, 0, range(7))
    finally:
        harness.close_window(window)

    assert row == ("1", "999", "Stub Entry", "1", "0:00:01", "2♣ 2♦ 2♥ 2♠ 3♣", "Stub hand")


@pytest.mark.parametrize(("checkbox_name", "expected"), CANVAS_PUBLISH_DEFAULTS)
def test_results_window_publish_checkbox_default_matches_the_authored_xrc(
    shared_results: ResultsWindow,
    checkbox_name: str,
    expected: bool,  # noqa: FBT001
) -> None:
    """Req 2: times off, laps board on, time board off, full/cards on.

    Asserted, never set here (results.xrc's own header comment
    records these five as already-authored canvas defaults) --
    proves the already-authored XRC, not any code this task wrote.
    """
    checkbox = harness.find_control(shared_results.frame, checkbox_name)

    assert checkbox.GetValue() is expected


def test_results_window_applies_the_canvas_minimum_width(shared_results: ResultsWindow) -> None:
    """D16: the canvas draws this window at 720px."""
    size = shared_results.frame.GetSize()

    assert size.width == results_win.MIN_SIZE[0]


def test_results_window_fits_within_1366x768(shared_results: ResultsWindow) -> None:
    """UX-DESKTOP §6: every window must fit the field-laptop floor."""
    size = shared_results.frame.GetSize()

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


def test_entry_detail_find_given_an_unknown_control_name_raises_naming_it(
    shared_entry_detail: EntryDetailDialog,
) -> None:
    """T-5: the one ``raise`` in ``views/entry_detail.py``."""
    with pytest.raises(LookupError, match=re.escape("no control named 'no_such_control'")):
        shared_entry_detail._find("no_such_control")


def test_results_window_find_given_an_unknown_control_name_raises_naming_it(
    shared_results: ResultsWindow,
) -> None:
    """T-5: the one ``raise`` in ``views/results_win.py``."""
    with pytest.raises(LookupError, match=re.escape("no control named 'no_such_control'")):
        shared_results._find("no_such_control")


# ------------------------------ repaint after model (unverified remedy)


def _spy_repaint(control: Any) -> tuple[MagicMock, MagicMock]:  # noqa: ANN401
    """Replace *control*'s Refresh/Update with spies; return both.

    Monkeypatching a real wx control's bound methods is a
    platform/GUI I/O boundary (T-10), the same category
    ``test_dialog_behavior.py``'s own ``_spy_on_set_focus`` already
    treats as legitimate to spy on directly in this codebase.

    *control* must stay referenced by a local in the caller for as
    long as the spy needs to see calls: measured (a throwaway probe
    script, per this repo's convention), wxPython's wrapper cache is
    weak, and a ``FindWindowByName`` result with no other surviving
    Python reference is collected -- the *next* lookup of the same
    control then builds a brand-new wrapper, missing this one's
    instance attributes entirely.
    """
    refresh, update = MagicMock(), MagicMock()
    control.Refresh = refresh
    control.Update = update
    return refresh, update


def test_ride_library_show_rides_repaints_the_list_after_associating_its_model(
    xrc_resource: object,
) -> None:
    """Unverified remedy; see ``associate_model``'s docstring."""
    window = harness.load_window(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
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
    window = harness.load_window(xrc_resource, ids.RIDER_EDITOR_DLG, frame=False)
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


def test_entry_detail_show_entry_repaints_both_dataviews_after_associating_models(
    xrc_resource: object,
) -> None:
    """Unverified remedy; see ``associate_model``'s docstring.

    ``show_entry`` associates two separate models (cards_list,
    laps_list) in one call -- both must repaint.
    """
    window = harness.load_window(xrc_resource, ids.ENTRY_DETAIL_DLG, frame=False)
    try:
        window.Show()
        harness.pump()
        # Both kept alive: _spy_repaint's docstring.
        cards_control = harness.find_control(window, ids.CARDS_LIST)
        laps_control = harness.find_control(window, ids.LAPS_LIST)
        cards_refresh, cards_update = _spy_repaint(cards_control)
        laps_refresh, laps_update = _spy_repaint(laps_control)
        view = EntryDetailDialog(window, "77", data_source=DemoDataSource())
        cards_count = view.cards_list.GetModel().GetCount()
        laps_count = view.laps_list.GetModel().GetCount()
    finally:
        harness.close_window(window)

    assert (cards_count, laps_count) == (len(CANVAS_CARDS_HELD_KEYS), len(CANVAS_LAPS))
    cards_refresh.assert_called_once_with()
    cards_update.assert_called_once_with()
    laps_refresh.assert_called_once_with()
    laps_update.assert_called_once_with()


def test_results_window_show_standings_repaints_the_list_after_associating_its_model(
    xrc_resource: object,
) -> None:
    """Unverified remedy; see ``associate_model``'s docstring."""
    window = harness.load_window(xrc_resource, ids.RESULTS_FRAME, frame=True)
    try:
        window.Show()
        harness.pump()
        # control kept alive: _spy_repaint's docstring.
        control = harness.find_control(window, ids.STANDINGS_LIST)
        refresh, update = _spy_repaint(control)
        view = ResultsWindow(window, data_source=DemoDataSource())
        row_count = view.standings_list.GetModel().GetCount()
    finally:
        harness.close_window(window)

    assert row_count == len(CANVAS_STANDINGS)
    refresh.assert_called_once_with()
    update.assert_called_once_with()
