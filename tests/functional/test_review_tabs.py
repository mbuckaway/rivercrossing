# SPDX-License-Identifier: GPL-3.0-only
"""WS-H functional tests: the console's two-tab review notebook.

``main.xrc`` replaced the "Needs review" static box with
``review_notebook``: page 1 ("Needs Review", the default) holds the
flagged crossings list + ``review_btn``; page 2 ("Riders") holds the
roster list with the double-click open seam. This module drives the
real window through the real harness (direct event injection per
``harness.py``), following ``test_console_live.py``'s structure: one
module-scoped console (one ``MainFrame`` construction decodes the
53-card imagelist and appends columns -- doing it per-test raises
this wx build's address-reuse hazard), built over a real
``RideEngine`` whose fixture already holds one flagged crossing, and
read-only assertions afterwards. The two state-mutating seams
(tab switching, ``review_btn``) set up their own starting selection
so no test depends on another's page state.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import harness
import pytest
import wx
import wx.dataview

from rivercrossing.cards import Shoe
from rivercrossing.ride import RideConfig, RideEngine
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui import ids, theme
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.presenters.data_source import EngineDataSource
from rivercrossing.ui.views import MainFrame, dialogs
from rivercrossing.ui.views.main_frame import (
    CONSOLE_RIDERS_LIST,
    FLAG_COL_LAP,
    FLAG_COL_LAP_TIME,
    FLAG_COL_PLATE,
    REVIEW_NOTEBOOK,
    RIDERS_COL_NAME,
    RIDERS_COL_PLATE,
    RIDERS_COL_TEAM,
)

pytestmark = pytest.mark.functional

# Notebook page changes apply in the notebook's own deferred pass;
# this bound is the event-driven settle for page-visibility assertions
# (mirrors the harness's other bounded settle loops).
_PAGE_SWITCH_SETTLE_ATTEMPTS = 25

_START = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)


def _fixture_roster() -> Roster:
    """Return the review fixture's roster (two solos + one pooled team).

    Shared by the console builder and the S1 end-to-end test (whose
    route context must pass the editor the *same roster shape* the
    console's Riders tab renders, so the activated plate resolves).
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Rider", last_name="12", plate="12")
    roster.create_solo_entry(first_name="Rider", last_name="34", plate="34")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    return roster


def _build_review_console(xrc_resource: object) -> MainFrame:
    """Build a RUNNING console whose engine holds one flagged lap.

    The fixture roster carries two solo riders and one pooled team so
    the Riders tab renders all four ``RiderRow`` shapes (solo rows
    with a dash Team cell, pooled members with the team name). Plate
    12's first lap is 5 s against a 60 s minimum, so it is the R-34
    flag the "Needs Review" tab must list; plate 34 then laps clean
    (newest first, so the flag is *not* the newest feed row -- the
    flagged subset must still find it).
    """
    roster = _fixture_roster()
    config = RideConfig(
        name="GORBA EPIC 2026",
        event_date=_START.date(),
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=_START,
        planned_duration_s=21600,
        min_lap_s=60,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    now = {"value": _START}

    def _clock() -> datetime:
        """Return the fixture's deterministic wall clock."""
        return now["value"]

    def _advance(seconds: float) -> None:
        """Move the fixture clock forward by *seconds*."""
        now["value"] = now["value"] + timedelta(seconds=seconds)

    engine = RideEngine(config=config, shoe=shoe, clock=_clock, roster=roster)
    engine.start()
    _advance(5)
    engine.record_crossing("12")  # flagged: 5 s < 60 s min lap
    _advance(100)
    engine.record_crossing("34")  # clean lap, newer than the flag
    source = EngineDataSource(engine, roster)
    window = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        console = MainFrame(window, data_source=source, resource=xrc_resource)
        presenter = ConsolePresenter(console, engine=engine, source=source)
        console.wire_entry(presenter.on_plate_entered)
        console.wire_console(presenter)
        console.set_state(source.ride_status())
        presenter.tick()  # drive the review lists through the presenter path
    except Exception:
        # Fault A: a construct-phase raise (a degraded load, a wiring
        # failure) must not leak the window the module fixture never
        # yields -- this builder raised before returning it.
        harness.close_window(window)
        raise
    return console


@pytest.fixture(scope="module")
def review_console(xrc_resource: object) -> MainFrame:
    """One live ``MainFrame`` for every review-tab assertion below."""
    console = _build_review_console(xrc_resource)
    try:
        yield console
    finally:
        # Drop the view before the window dies (Phase 2 reference
        # hygiene): the view holds every control wrapper, so leaving
        # it alive past close_window would keep their SIP map entries
        # from evicting (Addendum 2; test_console_demo's precedent).
        window = console.frame
        del console
        harness.release_main_window(wx.GetApp(), window)


def _notebook(review_console: MainFrame) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return the console's review notebook control."""
    return harness.find_control(review_console.frame, REVIEW_NOTEBOOK)


def _riders_model(review_console: MainFrame) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return the riders list's current model."""
    return harness.find_control(review_console.frame, CONSOLE_RIDERS_LIST).GetModel()


# --- the two pages ------------------------------------------------


def test_review_notebook_defaults_to_the_needs_review_tab(
    review_console: MainFrame,
) -> None:
    """WS-H: page 0 is "Needs Review", page 1 is "Riders"."""
    notebook = _notebook(review_console)

    assert notebook.GetSelection() == 0
    assert notebook.GetPageText(0) == "Needs Review"
    assert notebook.GetPageText(1) == "Riders"


def test_review_notebook_switching_to_riders_shows_only_that_page(
    review_console: MainFrame,
) -> None:
    """WS-H: selecting Riders shows that page and hides the flags."""
    notebook = _notebook(review_console)
    flagged_list = harness.find_control(review_console.frame, ids.FLAGGED_LIST)
    riders_list = harness.find_control(review_console.frame, CONSOLE_RIDERS_LIST)

    notebook.SetSelection(1)
    harness.pump()

    # Page flips are deferred on macOS: the notebook applies the
    # selection in its own page-change pass, so one pump is not enough
    # to hide the previous page. Settle until the flag list stops
    # rendering on screen -- an idle drain per attempt (never a bare
    # sleep; a bare SafeYield does not process the deferred idle work).
    # IsShownOnScreen, not IsShown: macOS tab views keep a non-selected
    # page's window flags set and hide it by clipping, so IsShown
    # still reports True for its children (measured).
    for _ in range(_PAGE_SWITCH_SETTLE_ATTEMPTS):
        if not flagged_list.IsShownOnScreen():
            break
        harness.flush_deferred_deletions()

    assert notebook.GetSelection() == 1
    assert riders_list.IsShownOnScreen()
    assert not flagged_list.IsShownOnScreen()


# --- the flagged tab ----------------------------------------------


def test_review_notebook_flagged_list_shows_only_the_flagged_crossing(
    review_console: MainFrame,
) -> None:
    """WS-H: the Needs Review tab lists the one short-lap crossing."""
    model = harness.find_control(review_console.frame, ids.FLAGGED_LIST).GetModel()

    assert model.GetCount() == 1
    assert model.GetValueByRow(0, FLAG_COL_PLATE) == "12"
    assert model.GetValueByRow(0, FLAG_COL_LAP) == "1"
    assert model.GetValueByRow(0, FLAG_COL_LAP_TIME) == "0:05"  # 5 s short lap


def test_review_btn_returns_to_the_needs_review_tab(review_console: MainFrame) -> None:
    """WS-H: Review… lands back on the flagged tab from either page."""
    notebook = _notebook(review_console)
    notebook.SetSelection(1)
    harness.pump()

    harness.click(review_console.frame, ids.REVIEW_BTN)

    assert notebook.GetSelection() == 0


# --- the riders tab -----------------------------------------------


def test_review_notebook_riders_list_declares_plate_name_team_columns(
    review_console: MainFrame,
) -> None:
    """WS-H: the Riders tab's three columns are Plate | Name | Team."""
    riders_list = harness.find_control(review_console.frame, CONSOLE_RIDERS_LIST)

    titles = [riders_list.GetColumn(col).GetTitle() for col in range(3)]

    assert titles == ["Plate", "Name", "Team"]


def test_review_notebook_riders_list_populates_every_roster_row(
    review_console: MainFrame,
) -> None:
    """WS-H: solos and pooled team members each get a row with cells."""
    model = _riders_model(review_console)

    assert model.GetCount() == 4
    assert (model.GetValueByRow(0, RIDERS_COL_PLATE), model.GetValueByRow(0, RIDERS_COL_NAME)) == (
        "12",
        "Rider 12",
    )
    assert model.GetValueByRow(0, RIDERS_COL_TEAM) == "—"  # solo dash
    assert model.GetValueByRow(2, RIDERS_COL_TEAM) == "Trail Blazers"
    assert model.GetValueByRow(3, RIDERS_COL_NAME) == "K. Singh"


def test_review_riders_activation_without_a_registered_callback_is_a_no_op(
    review_console: MainFrame,
) -> None:
    """WS-H: an unwired seam ignores activation without crashing.

    Runs before any test registers a callback: ``review_console`` is
    module-scoped and ``set_on_open_rider`` never clears, so this
    pin needs the console's initial unwired state.
    """
    riders_list = harness.find_control(review_console.frame, CONSOLE_RIDERS_LIST)
    model = _riders_model(review_console)
    item = model.GetItem(0)
    event = wx.dataview.DataViewEvent(wx.dataview.wxEVT_DATAVIEW_ITEM_ACTIVATED, riders_list, item)

    riders_list.GetEventHandler().ProcessEvent(event)
    harness.pump()

    assert review_console._on_open_rider is None


def test_review_riders_activation_fires_the_open_rider_callback_with_the_plate(
    review_console: MainFrame,
) -> None:
    """WS-H: double-clicking a rider row fires ``set_on_open_rider``.

    The activation event is posted directly (the harness's one working
    mechanism, ``harness.py``'s module docstring); the console itself
    opens nothing -- the app wires the open-entry-detail flow to this
    seam later.
    """
    riders_list = harness.find_control(review_console.frame, CONSOLE_RIDERS_LIST)
    opened: list[str] = []
    review_console.set_on_open_rider(opened.append)
    model = _riders_model(review_console)
    item = model.GetItem(2)  # A. Roy, pooled plate 77
    event = wx.dataview.DataViewEvent(wx.dataview.wxEVT_DATAVIEW_ITEM_ACTIVATED, riders_list, item)

    riders_list.GetEventHandler().ProcessEvent(event)
    harness.pump()

    assert opened == ["77"]


def test_review_riders_activation_opens_the_editor_preselected_at_the_plate(  # noqa: PLR0913, PLR0917 -- (review_console, xrc_resource, wx_app, monkeypatch): the four fixture seams
    review_console: MainFrame,
    xrc_resource: object,
    wx_app: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S1: the wired seam opens the editor at the activated plate.

    End-to-end proof of ux-polish's Riders-tab open flow
    (``app._wire_rider_open_seam`` -> ``_open_rider_editor_for``): a
    double-click on a live console's rider row opens the editor over
    the shared roster pre-selected at that rider -- the form shows
    the activated plate, not the blank add form. ``run_dialog`` is
    monkeypatched (``ShowModal`` would block with no user present,
    the suite's standard seam) and reads the form before returning.
    """
    roster = _fixture_roster()
    context = app_module._RouteContext(
        frame=review_console.frame,
        resource=xrc_resource,
        roster=roster,
        app=wx_app,
        theme_controller=theme.ThemeController(wx_app),
        presenter=review_console._presenter,
        console_view=review_console,
    )
    app_module._wire_rider_open_seam(context)
    opened: dict[str, str] = {}

    def _capture_and_close(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001
        opened["dialog"] = dialog.GetName()
        opened["plate"] = harness.find_control(dialog, ids.PLATE_INPUT).GetValue()
        return wx.ID_CANCEL

    monkeypatch.setattr(dialogs, "run_dialog", _capture_and_close)
    riders_list = harness.find_control(review_console.frame, CONSOLE_RIDERS_LIST)
    model = _riders_model(review_console)
    item = model.GetItem(2)  # A. Roy, pooled plate 77
    event = wx.dataview.DataViewEvent(wx.dataview.wxEVT_DATAVIEW_ITEM_ACTIVATED, riders_list, item)

    riders_list.GetEventHandler().ProcessEvent(event)
    harness.pump()

    assert opened == {"dialog": ids.RIDER_EDITOR_DLG, "plate": "77"}
