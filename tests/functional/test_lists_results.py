# SPDX-License-Identifier: GPL-3.0-only
"""Real-toolkit tests for ``results_frame`` (E1.5.2, E5.4.2, E6.4.1).

Split out of ``test_lists_demo.py`` -- alongside
``test_lists_entry_detail.py`` -- so the two heaviest functional
files spread their per-worker window churn across ``--dist loadfile``
workers (the wrapper-cache corruption remedy). ``ResultsWindow``
decorates an already-XRC-loaded window (``harness.load_window``, the
same pattern ``test_console_demo.py`` uses for ``MainFrame``) with
the code-side bindings xrc-windows.md's per-window footnotes assign
to it: the standings DataView's columns and rows, the ⚠ badge on
``draw_required`` tie rows, the code-side ``stale_infobar``, the
publish-checkbox defaults authored in results.xrc, the show-times
Total-column toggle, and ``tiebreak_list``'s live re-rank.

E5.4.2 retired the demo seam: results render the empty standings state
until E6 wires real placed rows, so the fixture below wires
``EmptyDataSource`` and the canvas-row pin became an empty-state pin.
E6.4.1 (P9) wires the real placed rows from a finished ride's live
``EngineDataSource`` (``finished_ride_view`` fixture) -- the same
source shape app.py's D10 RESULTS_FRAME branch now hands the window
-- plus the live-reorder and publish-option behavior. This suite is
read-and-write verified only: it runs in the Tart VM (AGENTS.md), not
on the macOS host.

The window carries no splitter, so the rebuild-and-compare hazard
this suite's harness warns about does not apply; it is built exactly
once per module and never torn down and rebuilt mid-module. The
constants and helpers shared with the other two list-window files
live in ``_lists_common``.
"""

import re
from datetime import date, datetime, timedelta

import harness
import pytest
import wx
from _lists_common import (
    CANVAS_PUBLISH_DEFAULTS,
    MAX_SCREEN_HEIGHT,
    MAX_SCREEN_WIDTH,
    _model_row,
    _spy_repaint,
)

from rivercrossing.cards import Shoe
from rivercrossing.ride import RideConfig, RideEngine
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.standings import DEFAULT_TIEBREAK_ORDER, TieBreak
from rivercrossing.ui import ids
from rivercrossing.ui.presenters.data_source import (
    EmptyDataSource,
    EngineDataSource,
    StandingsRow,
)
from rivercrossing.ui.views import results_win
from rivercrossing.ui.views.results_win import STALE_INFOBAR, ResultsWindow

pytestmark = pytest.mark.functional


# ----------------------------------------------------------- fixtures


class _FakeClock:
    """Wall-clock source a test ride can advance deterministically."""

    def __init__(self, start: datetime) -> None:
        """Start the fake clock at *start*."""
        self._now = start

    def __call__(self) -> datetime:
        """Return the current fake time."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the fake clock forward by *seconds*."""
        self._now = self._now + timedelta(seconds=seconds)


@pytest.fixture(scope="module")
def shared_results(xrc_resource: object) -> ResultsWindow:
    """One ``ResultsWindow``, reused by every read-only assertion."""
    window = harness.load_window_verified(xrc_resource, ids.RESULTS_FRAME, frame=True)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        view = ResultsWindow(window, data_source=EmptyDataSource())
        yield view
    finally:
        # Phase 2 reference hygiene: drop the view before the window
        # dies (see test_console_demo.py's shared_console finally).
        del view
        harness.close_window(window)


@pytest.fixture(scope="module")
def finished_ride_view(xrc_resource: object) -> ResultsWindow:
    """One ``ResultsWindow`` over a finished ride's live engine source.

    E6.4.1's live state: a real ``RideEngine`` with two recorded
    crossings, finished, read through the production
    ``EngineDataSource`` -- the exact source shape app.py's D10
    RESULTS_FRAME branch hands the window when a ride is open. The
    ride's stored ``tiebreak_order`` (the config default) seeds
    ``tiebreak_list``.
    """
    window = harness.load_window_verified(xrc_resource, ids.RESULTS_FRAME, frame=True)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
        roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
        roster.create_solo_entry(first_name="R.", last_name="Dubois", plate="8")
        config = RideConfig(
            name="GORBA EPIC 2026",
            event_date=date(2026, 9, 20),
            venue="Sea to Sky Gondola",
            lap_km=8.0,
            organizer="GORBA",
            scorer="K. Singh",
            planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- pre-persistence local, RideConfig's contract
            planned_duration_s=21600,
            min_lap_s=1,
            entry_mode=EntryMode.MIXED,
            plate_model=PlateModel.RIDER_POOLED,
        )
        shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
        clock = _FakeClock(config.planned_start)
        engine = RideEngine(config=config, shoe=shoe, clock=clock, roster=roster)
        engine.start()
        clock.advance(100)
        engine.record_crossing("123")
        clock.advance(100)
        engine.record_crossing("8")
        engine.finish()
        source = EngineDataSource(engine, roster)
        view = ResultsWindow(window, data_source=source, tiebreak_order=config.tiebreak_order)
        yield view
    finally:
        del view
        harness.close_window(window)


def _toggle_checkbox(view: ResultsWindow, control_name: str) -> None:
    """Flip *control_name* and post the ``EVT_CHECKBOX`` a click fires.

    ``wx.CheckBox.SetValue`` fires no ``EVT_CHECKBOX`` (measured
    harness convention); the event a real click would generate is
    posted directly, the same pattern test_ride_setup.py's own
    ``cap_chk`` toggle uses.
    """
    control = harness.find_control(view.frame, control_name)
    control.SetValue(not control.GetValue())
    event = wx.CommandEvent(wx.EVT_CHECKBOX.typeId, control.GetId())
    event.SetEventObject(control)
    control.GetEventHandler().ProcessEvent(event)
    harness.pump()


# ------------------------------------------------------ results_frame


def test_results_window_given_an_empty_source_shows_no_standings(
    shared_results: ResultsWindow,
) -> None:
    """E5.4.2/E6.4.1: no finished ride -- results render zero rows.

    The E5.4.2 empty-state pin stays: with ``EmptyDataSource`` (the
    D10 no-presenter path) the standings list is empty even though
    E6.4.1 wires live rows for a finished ride (``finished_ride_view``).
    """
    model = shared_results.standings_list.GetModel()

    rows = tuple(_model_row(model, row, range(7)) for row in range(model.GetCount()))

    assert rows == ()


def test_results_window_given_a_different_source_shows_its_rows_not_the_demo(
    xrc_resource: object,
) -> None:
    """Req 6: a no-op binding would keep showing demo rows, not this."""

    class _StubSource:
        def standings(
            self,
            order: tuple[TieBreak, ...] = DEFAULT_TIEBREAK_ORDER,  # noqa: ARG002 -- DataSource's signature, stub ignores it
        ) -> tuple[list[StandingsRow], list[StandingsRow]]:
            return [], [
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

    window = harness.load_window_verified(xrc_resource, ids.RESULTS_FRAME, frame=True)
    try:
        window.Show()
        harness.pump()
        view = ResultsWindow(window, data_source=_StubSource())
        model = view.standings_list.GetModel()
        header = _model_row(model, 0, range(7))
        row = _model_row(model, 1, range(7))
    finally:
        harness.close_window(window)

    assert header == ("", "", "Solo", "", "", "", "")
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


def test_results_window_find_given_an_unknown_control_name_raises_naming_it(
    shared_results: ResultsWindow,
) -> None:
    """T-5: the one ``raise`` in ``views/results_win.py``."""
    with pytest.raises(LookupError, match=re.escape("no control named 'no_such_control'")):
        shared_results._find("no_such_control")


# ------------------------------ repaint after model (unverified remedy)


def test_results_window_show_standings_repaints_the_list_after_associating_its_model(
    xrc_resource: object,
) -> None:
    """Unverified remedy; see ``associate_model``'s docstring."""
    window = harness.load_window_verified(xrc_resource, ids.RESULTS_FRAME, frame=True)
    try:
        window.Show()
        harness.pump()
        # control kept alive: _spy_repaint's docstring.
        control = harness.find_control(window, ids.STANDINGS_LIST)
        refresh, update = _spy_repaint(control)
        view = ResultsWindow(window, data_source=EmptyDataSource())
        row_count = view.standings_list.GetModel().GetCount()
    finally:
        harness.close_window(window)

    assert row_count == 0
    refresh.assert_called_once_with()
    update.assert_called_once_with()


# ---------------------------------------------- E6.4.1 live results


def test_results_window_given_a_finished_ride_source_shows_live_rows(
    finished_ride_view: ResultsWindow,
) -> None:
    """E6.4.1: live placed rows replace the empty state (D10/D16).

    Phase 3: the fixture ride is solo-only, so the list renders a Solo
    section header row followed by the two placed rows.
    """
    model = finished_ride_view.standings_list.GetModel()

    rows = tuple(_model_row(model, row, range(7)) for row in range(model.GetCount()))

    assert len(rows) == 3
    assert rows[0] == ("", "", "Solo", "", "", "", "")
    assert {row[1] for row in rows[1:]} == {"123", "8"}
    assert rows[1][0] == "1"  # best hand first, competition numbering
    assert all(row[6].startswith("High Card") for row in rows[1:])  # prose hand, not a class code


def test_results_window_tiebreak_list_is_seeded_from_the_rides_stored_order(
    finished_ride_view: ResultsWindow,
) -> None:
    """Tie-break seeding: the stored order's labels land in the list.

    The fixture ride carries the config default order; the seeded rows
    must be the three plain labels (no rank prefix -- it would go stale
    on reorder), in stored order.
    """
    tiebreak = harness.find_control(finished_ride_view.frame, ids.TIEBREAK_LIST)

    assert list(tiebreak.GetStrings()) == ["Most laps", "Total time", "High-card draw"]


def test_results_window_tie_rows_carry_the_warning_badge(xrc_resource: object) -> None:
    """R-43/xrc-windows.md D: a draw_required row shows the ⚠ badge.

    The engine fixture has no natural byte-identical tie (probed), so
    the badge is driven through the view's own ``show_standings`` with
    a draw pair -- the same view-capability approach the stub-source
    test uses. E6.4.1 renders the badge as a leading glyph in the
    Place cell (a new eighth column would shift the seven frozen
    canvas columns; the canvas shows no tie rows to pin one). Phase 3:
    the draw pair is passed as the Teams section, so the section
    header row precedes the two flagged rows.
    """
    window = harness.load_window_verified(xrc_resource, ids.RESULTS_FRAME, frame=True)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        view = ResultsWindow(window, data_source=EmptyDataSource())
        view.show_standings(
            [
                StandingsRow(
                    place=1,
                    plate="7",
                    entry="Luca Ferrari",
                    laps=10,
                    total="5:41:03",
                    best5=("AH", "KH", "QH", "JH", "TH"),
                    hand="Royal Flush",
                    draw_required=True,
                ),
                StandingsRow(
                    place=1,
                    plate="8",
                    entry="Fat Tire Four",
                    laps=9,
                    total="5:12:44",
                    best5=("AH", "KH", "QH", "JH", "TH"),
                    hand="Royal Flush",
                    draw_required=True,
                ),
            ],
            [],
        )
        model = view.standings_list.GetModel()
        header = _model_row(model, 0, range(7))
        first = _model_row(model, 1, range(7))
        second = _model_row(model, 2, range(7))
    finally:
        harness.close_window(window)

    assert header == ("", "", "Teams", "", "", "", "")
    assert first[0] == f"{results_win.TIE_BADGE} 1"
    assert second[0] == f"{results_win.TIE_BADGE} 1"


def test_results_window_stale_infobar_is_present_but_hidden(
    finished_ride_view: ResultsWindow,
) -> None:
    """E6.4.1: stale_infobar resolves by name, hidden, effects off.

    XRC cannot author a wxInfoBar (results.xrc's own header); the
    bar is built code-side with its frozen name and the mandatory
    no-slide effects (the measured ShowMessage/Dismiss hang). E7.3.2
    triggers it after reopened corrections; E6.4.1 only builds it.
    """
    bar = harness.find_control(finished_ride_view.frame, STALE_INFOBAR)

    assert (bar.GetName(), bar.IsShown()) == (STALE_INFOBAR, False)
    assert (bar.GetShowEffect(), bar.GetHideEffect()) == (
        wx.SHOW_EFFECT_NONE,
        wx.SHOW_EFFECT_NONE,
    )


def test_results_window_show_times_toggle_hides_total_column_and_updates_presenter_options(
    finished_ride_view: ResultsWindow,
) -> None:
    """R-63's UI proof: the times checkbox feeds the export options.

    Toggling ``show_times_chk`` on both reveals the Total column
    (results.xrc's code-side footnote) and makes the presenter report
    ``show_times=True`` -- the exact options E6.4.2's export handlers
    will read.
    """
    assert finished_ride_view._total_column.IsHidden() is True
    assert finished_ride_view.presenter.export_options().show_times is False

    _toggle_checkbox(finished_ride_view, ids.SHOW_TIMES_CHK)

    assert finished_ride_view._total_column.IsHidden() is False
    assert finished_ride_view.presenter.export_options().show_times is True
