# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for E7.2.2: REOPENED corrections-only mode.

spec §3 (R-36): a finished ride reopens into a distinct,
corrections-only state -- the clock stays closed and live plate entry
stays off, the correction menu rows (and Finish Ride) stay enabled,
corrected crossings render highlighted in the feed, ``reopened_infobar``
tells the operator the ride is open for corrections, and the single
primary action is "Finish again" -- the existing finish route,
re-labelled, re-locking to FINISHED through the existing finish gate
and re-ranking standings via the existing ``standings.rank`` (never
reimplemented here).

What only a real, loaded wx session can prove lives here (the engine
rules themselves are unit-tested in ``test_console.py`` /
``test_commands.py``):

* the plate entry row and Record button are disabled in REOPENED;
* the live menubar enables the correction rows + Finish Ride and
  disables Start/Stop/Reopen in REOPENED;
* a corrected crossing's feed row renders bold (the E7.2.2 marker
  channel, read off ``DataViewIndexListModel.GetAttrByRow``);
* ``reopened_infobar`` is visible in REOPENED and dismissed on
  finish-again;
* firing ``mi_finish_ride`` from REOPENED shows the finish confirm
  re-labelled "Finish again", and a confirmed finish re-locks to
  FINISHED with re-ranked standings.

Like the rest of ``tests/functional/`` these run only in the Tart VM,
never directly on the host (the suite opens real wx windows).
"""

from datetime import date, datetime
from typing import Any

import harness
import pytest
import wx
import wx.dataview
import wx.xrc

from rivercrossing.cards import Card, Shoe
from rivercrossing.ride import RideConfig, RideEngine, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui import feed_model, ids, theme
from rivercrossing.ui.presenters import console as console_module
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.presenters.data_source import EngineDataSource
from rivercrossing.ui.views import MainFrame
from rivercrossing.ui.views.main_frame import REOPENED_INFOBAR

pytestmark = pytest.mark.functional

# The seeded shoe this scenario is deterministic on: crossing #1 deals
# 8C (plate 12), #2 deals TH (plate 34), #3 deals QD (plate 12) -- the
# mini-acceptance provenance (test_mini_acceptance.py's module
# docstring records deal 0 = 8C, deal 1 = TH, deal 2 = QD). So plate
# 12 holds queen-high (8C QD, 2 laps), plate 34 ten-high (TH, 1 lap);
# voiding 12's QD leaves 12 eight-high and 34 wins -- a deterministic
# standings change for the finish-again assertion.
SEED = 20260920
LEADER_PLATE = "12"
LEADER_WINNING_CARD = "QD"


class _ScenarioClock:
    """An advanceable naive datetime clock for the reopened scenario."""

    def __init__(self, start: datetime) -> None:
        """Start at *start* (the planned start instant)."""
        self._now = start

    def __call__(self) -> datetime:
        """Return the current scenario time."""
        return self._now


def _build_ride_console(
    xrc_resource: object,
    *,
    reopen: bool,
) -> tuple[Any, MainFrame, ConsolePresenter, RideEngine, EngineDataSource]:
    """Build a live console over a finished (optionally reopened) ride.

    Wires ``MainFrame`` + ``ConsolePresenter`` + ``EngineDataSource``
    exactly as the app bootstrap does, binds every §15 route and
    applies the menu binder for the ride's current state -- so the
    finish-again route and the live menubar are both under test.
    Returns ``(window, console, presenter, engine, source)``.
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Rider", last_name="12", plate="12")
    roster.create_solo_entry(first_name="Rider", last_name="34", plate="34")
    config = RideConfig(
        name="E7.2.2 Reopened Mode",
        event_date=date(2026, 9, 20),
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- scenario clock is naive
        planned_duration_s=21600,
        min_lap_s=1,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=SEED)
    clock = _ScenarioClock(config.planned_start)
    engine = RideEngine(config=config, shoe=shoe, clock=clock, roster=roster)
    engine.start()
    engine.record_crossing("12", at=datetime(2026, 9, 20, 10, 30))  # noqa: DTZ001
    engine.record_crossing("34", at=datetime(2026, 9, 20, 10, 32))  # noqa: DTZ001
    engine.record_crossing("12", at=datetime(2026, 9, 20, 10, 35))  # noqa: DTZ001
    engine.finish()
    if reopen:
        engine.reopen()
    source = EngineDataSource(engine, roster)

    window = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    window.Show()
    window.Layout()
    harness.pump()
    menubar = harness.load_menubar(xrc_resource, ids.MAIN_MENUBAR)
    window.SetMenuBar(menubar)
    console = MainFrame(window, data_source=source, resource=xrc_resource)
    presenter = ConsolePresenter(console, engine=engine, source=source)
    console.wire_entry(presenter.on_plate_entered)
    console.wire_console(presenter)
    console.set_state(source.ride_status())
    console.focus_entry()

    context = app_module._RouteContext(
        frame=window,
        resource=xrc_resource,
        roster=roster,
        app=wx.GetApp(),
        theme_controller=theme.ThemeController(wx.GetApp()),
        presenter=presenter,
        console_view=console,
        detail_plate="12",
    )
    app_module._bind_routes(context)
    app_module._apply_menu_state(context, engine.state)
    return window, console, presenter, engine, source


def _menu_item_enabled(window: Any, item_id: str) -> bool:  # noqa: ANN401
    """Return whether the menubar item named *item_id* is enabled."""
    menubar = window.GetMenuBar()
    item, _menu = menubar.FindItem(wx.xrc.XRCID(item_id))
    return item is not None and item.IsEnabled()


def _feed_model_rows(window: Any) -> tuple[tuple[str, int, bool], ...]:  # noqa: ANN401
    """Read each feed row as ``(plate, lap, bold)``.

    Bold is the E7.2.2 edited-row channel: ``GetAttrByRow`` sets it
    for both flagged (R-34) and edited (corrected) crossings, the same
    visual vocabulary the mini-acceptance feed probe uses.
    """
    model = harness.find_control(window, ids.CROSSINGS_LIST).GetModel()
    rows = []
    for row in range(model.GetCount()):
        attr = wx.dataview.DataViewItemAttr()
        bold = model.GetAttrByRow(row, feed_model.COL_TIME, attr) and bool(attr.GetBold())
        rows.append(
            (
                model.GetValueByRow(row, feed_model.COL_PLATE),
                int(model.GetValueByRow(row, feed_model.COL_LAP)),
                bold,
            )
        )
    return tuple(rows)


def test_reopened_mode_entry_disabled_and_corrections_enabled(
    xrc_resource: object,
) -> None:
    """REOPENED: no live entry, corrections + Finish on, Start off."""
    window, console, _presenter, engine, _source = _build_ride_console(xrc_resource, reopen=True)
    try:
        assert harness.find_control(window, ids.RIDE_STATUS_LBL).GetLabelText() == "REOPENED"
        assert harness.find_control(window, ids.PLATE_INPUT).IsEnabled() is False
        assert harness.find_control(window, ids.RECORD_BTN).IsEnabled() is False
        # No live crossings in REOPENED -- the engine refuses entry.
        assert engine.record_crossing("12").accepted is False

        for item_id in (
            ids.MI_ADD_CROSSING_AT,
            ids.MI_EDIT_CROSSING,
            ids.MI_REASSIGN_PLATE,
            ids.MI_DEAL_MANUAL,
            ids.MI_VOID_CARD,
            ids.MI_MARK_DNF,
            ids.MI_FINISH_RIDE,
        ):
            assert _menu_item_enabled(window, item_id) is True, item_id
        for item_id in (ids.MI_START_RIDE, ids.MI_STOP_RIDE, ids.MI_REOPEN_RIDE):
            assert _menu_item_enabled(window, item_id) is False, item_id
    finally:
        del console
        harness.close_window(window)


def test_reopened_mode_reopened_infobar_visible_after_reopen(
    xrc_resource: object,
) -> None:
    """REOPENED shows ``reopened_infobar``; finish-again clears it."""
    window, console, _presenter, engine, _source = _build_ride_console(xrc_resource, reopen=True)
    try:
        infobar = wx.Window.FindWindowByName(REOPENED_INFOBAR, window)
        assert infobar is not None
        assert infobar.IsShown() is True

        engine.finish()  # finish again -- the infobar clears
        console.set_state(engine.state)
        assert infobar.IsShown() is False
        assert harness.find_control(window, ids.RIDE_STATUS_LBL).GetLabelText() == "FINISHED"
    finally:
        del console
        harness.close_window(window)


def test_reopened_mode_corrected_crossing_highlighted_in_feed(
    xrc_resource: object,
) -> None:
    """An edited crossing's row renders bold; siblings stay clear."""
    window, console, presenter, engine, _source = _build_ride_console(xrc_resource, reopen=True)
    try:
        before = _feed_model_rows(window)
        assert all(not bold for _plate, _lap, bold in before)

        engine.edit_crossing("12", 1, datetime(2026, 9, 20, 10, 31), "mis-keyed time")  # noqa: DTZ001
        presenter.tick()  # the correction handler's own refresh

        rows = _feed_model_rows(window)
        by_lap = {(plate, lap): bold for plate, lap, bold in rows}
        assert by_lap[("12", 1)] is True  # the edited lap
        assert by_lap[("12", 2)] is False
        assert by_lap[("34", 1)] is False
    finally:
        del console
        harness.close_window(window)


def test_reopened_mode_finish_again_relabels_dialog_relocks_and_reranks(
    xrc_resource: object,
) -> None:
    """Finish again: "Finish again" confirm re-locks and re-ranks.

    Reopened corrections change the snapshot (voiding plate 12's
    winning QD leaves it eight-high); the existing finish route, shown
    with the REOPENED "Finish again" label, re-locks to FINISHED and
    the standings re-rank through standings.rank -- plate 34 leads.
    """
    window, console, _presenter, engine, source = _build_ride_console(xrc_resource, reopen=True)
    try:
        before = source.standings()
        assert before[0].plate == LEADER_PLATE
        engine.void_card(LEADER_PLATE, Card.parse(LEADER_WINNING_CARD), "wrong card off the line")

        captured: dict[str, str | None] = {}
        original_gate = console_module.FINISH_GATE
        consulted: list[bool] = []

        def _recording_gate() -> bool:
            consulted.append(True)
            return original_gate()

        def _drive(dialog: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
            captured["title"] = dialog.GetTitle()
            ok_button = wx.Window.FindWindowById(wx.ID_OK, dialog)
            captured["ok_label"] = ok_button.GetLabel() if ok_button is not None else None
            harness.click(dialog, "wxID_OK")

        console_module.FINISH_GATE = _recording_gate
        try:
            wx.CallAfter(_drive)
            harness.fire_menu_event(window, ids.MI_FINISH_RIDE)
        finally:
            console_module.FINISH_GATE = original_gate

        assert consulted == [True]
        assert captured["title"] == "Finish again?"
        assert captured["ok_label"] == "Finish again"
        assert engine.state is RideStatus.FINISHED
        assert harness.find_control(window, ids.RIDE_STATUS_LBL).GetLabelText() == "FINISHED"
        assert harness.find_control(window, ids.PLATE_INPUT).IsEnabled() is False
        infobar = wx.Window.FindWindowByName(REOPENED_INFOBAR, window)
        assert infobar is not None
        assert infobar.IsShown() is False

        after = source.standings()
        assert [row.plate for row in after] != [row.plate for row in before]
        assert after[0].plate == "34"

        # The E6.4.1 results window re-ranks from the live source: a
        # fresh Standings (mi_standings route) reads the corrected
        # leader at the top -- ranking stays standings.rank's job.
        harness.fire_menu_event(window, ids.MI_STANDINGS)
        results_frame = wx.Window.FindWindowByName(ids.RESULTS_FRAME)
        assert results_frame is not None
        try:
            model = harness.find_control(results_frame, ids.STANDINGS_LIST).GetModel()
            assert model.GetCount() == 2
            assert model.GetValueByRow(0, 1) == "34"  # COL_PLATE
        finally:
            harness.close_window(results_frame)
    finally:
        del console
        harness.close_window(window)
