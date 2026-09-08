# SPDX-License-Identifier: GPL-3.0-only
"""Functional coverage walk over every §15 route (E1.4.1, R-73).

R-73: "the menu-coverage test walks all §15 routes in all ride
states." Only the parts of the walk that genuinely need a real,
loaded ``wx.MenuBar`` live here -- the route table's own shape
(row/menu counts, kind/target transcription, ``route_for_id``'s
dispatch and its negative path) needs no display at all and lives in
``tests/unit/ui/test_commands.py`` instead (mirroring the split
``cards_imagelist`` already uses).

What only this module can prove:

1. A real ``wx.CommandEvent(wx.EVT_MENU, ...)`` posted at a real,
   loaded menubar's real id actually reaches ``commands.ROUTE_TABLE``'s
   matching row -- what R-73 means by "reachable and drivable".
   ``main.xrc``'s own declared shape (46 ``mi_*`` names + 3 stock ids,
   per-menu item counts, accelerators) is already locked down by
   ``test_xrc_structure.py``; this module drives real events at it.
2. What wx actually does with the macOS stock-item relocation in this
   harness session (measured, not assumed).
3. That the accelerator table agrees with ``main.xrc``'s live
   ``<accel>`` declarations, via a real ``wx.MenuItem.GetAccel()``.
4. ux-polish wired the three COMMAND rows that used to post the
   "not yet implemented" stub -- Start Ride, Back Up Database…,
   Review Held Cards -- so the second section below drives them at a
   real ``build_main_window`` frame and pins what each does instead
   (the presenter start gate, the no-store guard, the review-tab
   focus). The Results window's ``reopen_btn`` gets the same
   treatment: clicking it runs the reopen flow the menu row runs.
5. ux-polish's final dead-row audit wired the last two Ride ▸ rows
   (Stop Ride…, Set Start Time…) to real confirm/form flows; the
   wired-row section drives them at a real bound frame (auto-OK /
   auto-Cancel through the ``dialogs.run_dialog`` seam), pins S2's
   incomplete-setup start gate (a DRAFT ride with one rider but a
   blank minimum field stays DRAFT with the refusal notice), and
   exercises the Settings dialog's ``backup_now_btn`` seam (S5) --
   the R-54 handler's second entry point.

``wx.xrc.XRCID(name)`` is measured (a throwaway probe, per
harness.py's own convention) to return the *same* runtime int id a
loaded resource assigned to *name*, for both ``mi_*`` names and the
``wxID_*`` stock names -- but only once that name has been loaded at
least once in this process. The session-scoped ``xrc_resource``
fixture guarantees that before any test here runs.
"""

from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import harness
import pytest
import wx
import wx.xrc

from rivercrossing.cards import Shoe
from rivercrossing.ride import RideConfig, RideEngine, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.store import Store
from rivercrossing.ui import accelerators, commands, ids, theme
from rivercrossing.ui import app as app_module
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.presenters.data_source import EngineDataSource
from rivercrossing.ui.views import MainFrame, dialogs
from rivercrossing.ui.views.main_frame import REVIEW_NOTEBOOK

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.functional

COVERAGE_CASES = tuple((route, item_id) for route in commands.ROUTE_TABLE for item_id in route.ids)
COVERAGE_CASE_IDS = [f"{route.menu}:{item_id}" for route, item_id in COVERAGE_CASES]

STOCK_RELOCATION_CASES = (
    (wx.ID_ABOUT, "&Help"),
    (wx.ID_PREFERENCES, "&File"),
    (wx.ID_EXIT, "&File"),
)

XRC_ACCELERATOR_CASES = tuple(
    (accelerator.menu_item_id, accelerator.key)
    for accelerator in accelerators.ACCELERATOR_TABLE
    if accelerator.menu_item_id is not None
)


@pytest.fixture
def frame_with_menubar(xrc_resource: object) -> Iterator[tuple[Any, Any]]:
    """Load main_frame with its real menubar attached, then close it."""
    frame = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    try:
        menubar = harness.load_menubar(xrc_resource, ids.MAIN_MENUBAR)
        frame.SetMenuBar(menubar)
        harness.pump()
        yield frame, menubar
    finally:
        # Fault A: the load+construct phase sits inside this finally
        # (a menubar load or attach failure must not leak the frame).
        harness.close_window(frame)


def _real_id(name: str) -> int:
    """Return the runtime wx id XRC assigned to *name*.

    Measured idempotent once *name* has been loaded (see the module
    docstring): a safe way back from a frozen XRC/stock name to the
    int id a real ``wx.MenuItem`` carries.
    """
    return int(wx.xrc.XRCID(name))


@pytest.mark.parametrize(("route", "item_id"), COVERAGE_CASES, ids=COVERAGE_CASE_IDS)
def test_menu_route_is_reachable_and_resolves_its_declared_kind(
    frame_with_menubar: object, route: commands.MenuRoute, item_id: str
) -> None:
    """Every §15 route's real menu id delivers EVT_MENU and resolves."""
    frame, _menubar = frame_with_menubar
    real_id = _real_id(item_id)
    delivered_ids: list[int] = []
    frame.Bind(wx.EVT_MENU, lambda evt: delivered_ids.append(evt.GetId()), id=real_id)
    event = wx.CommandEvent(wx.EVT_MENU.typeId, real_id)
    event.SetEventObject(frame)

    frame.GetEventHandler().ProcessEvent(event)
    harness.pump()

    resolved = commands.route_for_id(item_id)
    assert delivered_ids == [real_id]
    assert resolved is route


@pytest.mark.parametrize(("stock_id", "authored_menu_title"), STOCK_RELOCATION_CASES)
def test_stock_menu_item_stays_in_its_authored_menu_in_this_harness(
    frame_with_menubar: object, stock_id: int, authored_menu_title: str
) -> None:
    """Measured: no macOS app-menu relocation in this pytest session.

    wx documents that About / Preferences / Exit move into the native
    application menu on macOS, but that needs a foregrounded
    NSApplication. This harness's ``wx.App`` is never the active,
    focused app (harness.py's own module docstring measures the same
    fact about ``wx.UIActionSimulator``), so at the ``wx.MenuBar``
    object level -- the only level a headless functional test can
    observe -- ``wxID_ABOUT``/``wxID_PREFERENCES``/``wxID_EXIT`` are
    found still attached to the menu ``main.xrc`` declared them in,
    not moved to a distinct application menu (measured on wxPython
    4.3.1 / wxWidgets 3.3.3, macOS-cocoa). This reports what wx
    actually does here, not the documented behaviour of a real,
    foregrounded app bundle, which this suite cannot observe.
    """
    _frame, menubar = frame_with_menubar

    item, containing_menu = menubar.FindItem(stock_id)

    assert item.GetId() == stock_id
    assert containing_menu.GetTitle() == authored_menu_title


@pytest.mark.parametrize(("menu_item_id", "key"), XRC_ACCELERATOR_CASES)
def test_accelerator_table_agrees_with_the_xrc_declared_accel(
    frame_with_menubar: object, menu_item_id: str, key: str
) -> None:
    """Ctrl+Z, F5, F1: the table agrees with main.xrc's <accel>."""
    _frame, menubar = frame_with_menubar
    real_id = _real_id(menu_item_id)

    item, _menu = menubar.FindItem(real_id)

    assert item.GetAccel().ToString() == key


# --- ux-polish: the wired COMMAND rows no longer post the stub ------
#
# The section above only proves each id is reachable on a real
# menubar; this one proves the three rows ux-polish wired actually act
# when driven at a real, fully bound bootstrap frame (routes bound via
# _bind_routes, live presenter threaded) instead of posting "not yet
# implemented".


def _fire_menu_event(frame: Any, item_id: str) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Post a real ``EVT_MENU`` for *item_id* at *frame*, then settle.

    Delegates to :func:`harness.fire_menu_event`, the shared home of
    this and ``test_app_bootstrap.py``'s identical helper (module
    docstrings there).
    """
    harness.fire_menu_event(frame, item_id)


@pytest.fixture(scope="module")
def app_frame(wx_app: object) -> Any:  # noqa: ANN401 -- ordering only, see docstring
    """One store-less ``build_main_window`` frame the wired tests share.

    The bootstrap console is DRAFT over an empty roster (E5.4.2) --
    the state Start Ride's gate refuses and Review Held Cards focuses
    from -- and there is no store, which is what Back Up Database…'s
    guard notices. Firing the three events here never opens a modal
    (both refuse/notice paths return), so one module-scoped instance
    serves every test.
    """
    frame = app_module.build_main_window(wx_app)
    try:
        yield frame
    finally:
        harness.close_window(frame)


def test_mi_start_ride_runs_the_presenter_start_gate_not_the_stub(
    app_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Start Ride over the empty DRAFT console surfaces the engine gate.

    ux-polish wired ``mi_start_ride`` to ``presenter.on_start()``; the
    bootstrap engine's own start gate refuses the empty roster and the
    presenter posts the refusal -- proof the route no longer falls
    through to "not yet implemented".
    """
    _fire_menu_event(app_frame, "mi_start_ride")

    status_text = app_frame.GetStatusBar().GetStatusText()

    assert status_text == "Cannot start: roster has no riders"


def test_mi_backup_now_posts_the_no_store_guard_notice(
    app_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Back Up Database… with no store posts the guard, not the stub."""
    route = commands.route_for_id("mi_backup_now")
    _fire_menu_event(app_frame, "mi_backup_now")

    status_text = app_frame.GetStatusBar().GetStatusText()

    assert status_text == f"{route.label} — no store is open"


def test_mi_backup_now_with_a_store_writes_a_backup_and_posts_its_path(
    xrc_resource: object,
    wx_app: object,
    tmp_path: object,
) -> None:
    """Back Up Database… over a live store writes the backup (R-54).

    The wired route's success arm: ``Store.backup_now`` writes one
    timestamped file into the ``<db>.backups/`` sibling directory and
    the status bar posts the written path.
    """
    db_path = Path(str(tmp_path)) / "rides.db"
    store = Store.open(db_path)
    context, _engine = _build_live_console(xrc_resource, wx_app, store=store, finished=True)
    try:
        _fire_menu_event(context.frame, "mi_backup_now")

        status_text = context.frame.GetStatusBar().GetStatusText()
        backup_dir = Path(f"{db_path}.backups")
        backups = sorted(backup_dir.glob("*.db")) if backup_dir.is_dir() else []

        assert len(backups) == 1
        assert status_text == f"Backed up database to {backups[-1]}"
    finally:
        store.close()
        harness.close_window(context.frame)


def test_mi_review_held_returns_the_review_notebook_to_the_needs_review_tab(
    app_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Review Held Cards focuses the flagged tab from the Riders tab.

    The menu is wired to ``context.console_view.focus_review_panel()``:
    switching the notebook to the Riders page first, then firing the
    route, must land back on the "Needs Review" tab (page 0).
    """
    notebook = harness.find_control(app_frame, REVIEW_NOTEBOOK)
    notebook.SetSelection(1)
    harness.pump()

    _fire_menu_event(app_frame, "mi_review_held")

    assert notebook.GetSelection() == 0


# --- ux-polish: the results frame's reopen_btn ----------------------


def _build_live_console(  # noqa: PLR0913 -- (xrc_resource, wx_app, store, venue, started, finished, reopened): the live-console builder's state knobs
    xrc_resource: object,
    wx_app: object,
    *,
    store: Store | None = None,
    venue: str = "Sea to Sky Gondola",
    started: bool = True,
    finished: bool = False,
    reopened: bool = False,
) -> tuple[app_module._RouteContext, RideEngine]:
    """Build a bound app context over one live engine state.

    Mirrors ``test_results_exports.py``'s own live-context builder:
    a real ``main_frame`` with its menubar, a live console + presenter
    over an engine built from the knobbed state (one rider clears the
    start gate), and a ``_RouteContext`` with the routes bound the way
    ``build_main_window`` binds them. The state knobs: ``started=False``
    leaves the engine DRAFT (S2's start-gate walk); ``finished`` moves
    past RUNNING to FINISHED (the reopen-flow precondition);
    ``reopened`` implies a finish first and lands in REOPENED; *venue*
    lets S2 blank a minimum setup field. ``store`` threads a live
    Store into the context when the caller wants the store-backed
    routes (the backup tests).
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Rider", last_name="12", plate="12")
    config = RideConfig(
        name="GORBA EPIC 2026",
        event_date=date(2026, 9, 20),
        venue=venue,
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive, RideConfig's own contract
        planned_duration_s=21600,
        min_lap_s=1,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    engine = RideEngine(
        config=config,
        shoe=shoe,
        clock=lambda: datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive clock, matching the naive instants
        roster=roster,
    )
    if started:
        engine.start()
    if reopened or finished:
        engine.finish()
    if reopened:
        engine.reopen()
    source = EngineDataSource(engine, roster)
    frame = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    try:
        menubar = harness.load_menubar(xrc_resource, ids.MAIN_MENUBAR)
        frame.SetMenuBar(menubar)
        frame.Show()
        frame.Layout()
        harness.pump()
        console = MainFrame(frame, data_source=source, resource=xrc_resource)
        presenter = ConsolePresenter(console, engine=engine, source=source)
        console.wire_entry(presenter.on_plate_entered)
        console.wire_console(presenter)
        console.set_state(source.ride_status())
        context = app_module._RouteContext(
            frame=frame,
            resource=xrc_resource,
            roster=roster,
            app=wx_app,
            theme_controller=theme.ThemeController(wx_app),
            presenter=presenter,
            console_view=console,
            store=store,
        )
        app_module._bind_routes(context)
        app_module._apply_menu_state(context, engine.state)
    except Exception:
        # Fault A: a construct-phase raise (a degraded load, a wiring
        # failure) must not leak the frame the caller's finally never
        # sees -- this builder raised before returning it.
        harness.close_window(frame)
        raise
    return context, engine


def test_results_reopen_btn_runs_the_same_reopen_flow_as_the_menu(
    xrc_resource: object,
    wx_app: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clicking ``reopen_btn`` opens the confirm and reopens the ride.

    ux-polish wired the results frame's Reopen ride… button to the
    same ``_handle_reopen_ride_route`` flow ``mi_reopen_ride`` runs:
    the confirm dialog opens (auto-OK here -- ``ShowModal`` would
    block with no user present, the suite's standard seam) and a
    confirmed reopen moves the FINISHED console engine to REOPENED.
    """
    context, engine = _build_live_console(xrc_resource, wx_app, finished=True)
    results_frame = None
    opened: list[str] = []

    def _auto_ok(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001
        opened.append(dialog.GetName())
        return wx.ID_OK

    try:
        monkeypatch.setattr(dialogs, "run_dialog", _auto_ok)
        harness.fire_menu_event(context.frame, ids.MI_STANDINGS)
        results_frame = wx.FindWindowByName(ids.RESULTS_FRAME)
        assert results_frame is not None

        harness.click(results_frame, ids.REOPEN_BTN)

        status_label = harness.find_control(context.frame, ids.RIDE_STATUS_LBL)
        assert engine.state is RideStatus.REOPENED
        assert opened == [ids.REOPEN_RIDE_DLG]
        assert status_label.GetLabelText() == "REOPENED"
    finally:
        if results_frame is not None:
            harness.close_window(results_frame)
        harness.close_window(context.frame)


# --- ux-polish: the last two dead Ride ▸ rows ----------------------
#
# The final dead-control audit found two menu rows whose DIALOG
# targets still opened through _open_target's generic path -- Ride ▸
# Stop Ride… and Ride ▸ Set Start Time… appeared and a confirmed OK
# did nothing. The section below drives both wired handlers at a real,
# bound bootstrap frame (routes bound via _bind_routes, live presenter
# threaded), plus S2 (Start Ride's incomplete-setup gate) and S5 (the
# Settings dialog's backup_now_btn seam).


def test_mi_stop_ride_confirmed_runs_the_presenter_stop_flow(
    xrc_resource: object,
    wx_app: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ride ▸ Stop Ride…: the confirm opens and OK stops the ride.

    The wired row runs the identical confirm -> ``on_stop_confirmed``
    flow the console Stop button runs (R-35): the ``stop_confirm_dlg``
    opens (auto-OK, the suite's standard seam) and a confirmed OK
    locks plate entry while the ride stays RUNNING and posts the
    presenter's notice -- proof the row no longer opens the dialog and
    does nothing.
    """
    context, engine = _build_live_console(xrc_resource, wx_app)
    opened: list[str] = []

    def _auto_ok(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001
        opened.append(dialog.GetName())
        return wx.ID_OK

    try:
        monkeypatch.setattr(dialogs, "run_dialog", _auto_ok)
        harness.fire_menu_event(context.frame, "mi_stop_ride")

        status_text = context.frame.GetStatusBar().GetStatusText()
        status_label = harness.find_control(context.frame, ids.RIDE_STATUS_LBL)
        assert opened == [ids.STOP_CONFIRM_DLG]
        assert engine.stopped is True
        assert engine.state is RideStatus.RUNNING  # stop is a guard, not a state
        assert status_text == "Ride stopped — continue to resume"
        assert status_label.GetLabelText() == "RUNNING"
    finally:
        harness.close_window(context.frame)


def test_mi_stop_ride_cancelled_leaves_the_ride_running(
    xrc_resource: object,
    wx_app: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled stop confirm changes nothing (R-35's guard)."""
    context, engine = _build_live_console(xrc_resource, wx_app)
    opened: list[str] = []

    def _auto_cancel(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001
        opened.append(dialog.GetName())
        return wx.ID_CANCEL

    try:
        monkeypatch.setattr(dialogs, "run_dialog", _auto_cancel)
        harness.fire_menu_event(context.frame, "mi_stop_ride")

        assert opened == [ids.STOP_CONFIRM_DLG]
        assert engine.stopped is False
        assert engine.state is RideStatus.RUNNING
    finally:
        harness.close_window(context.frame)


def test_mi_set_start_time_confirmed_backdates_the_ride_start(
    xrc_resource: object,
    wx_app: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set Start Time…: prefilled pickers; OK back-dates actual_start.

    The wired row runs ``set_start_dlg`` prefilled with the ride's
    planned start (10:00 on the 20th); the operator moves both pickers
    to 09:55 on the 21st and confirms. ``engine.set_start_time``
    applies the gun-missed correction (spec §3, 3d) -- the audit trail
    records the back-dated ``actual_start`` with the previous start --
    the console refreshes and the status bar confirms. The date picker
    is moved too, so a handler that read only the time picker fails
    the assertion.
    """
    context, engine = _build_live_console(xrc_resource, wx_app)
    opened: list[str] = []
    picked: dict[str, tuple[int, ...]] = {}

    def _move_pickers_and_ok(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001
        opened.append(dialog.GetName())
        date_picker = harness.find_control(dialog, ids.START_DATE_PICKER)
        time_picker = harness.find_control(dialog, ids.START_TIME_PICKER)
        prefilled_date = date_picker.GetValue()
        prefilled_time = time_picker.GetValue()
        picked["prefill_date"] = (
            prefilled_date.GetYear(),
            prefilled_date.GetMonth() + 1,
            prefilled_date.GetDay(),
        )
        picked["prefill_time"] = (prefilled_time.GetHour(), prefilled_time.GetMinute())
        later_date = wx.DateTime()
        later_date.Set(21, 8, 2026)  # wx months are 0-based: 2026-09-21
        date_picker.SetValue(later_date)
        earlier_time = wx.DateTime()
        earlier_time.SetHMS(9, 55, 0)
        time_picker.SetValue(earlier_time)
        return wx.ID_OK

    try:
        monkeypatch.setattr(dialogs, "run_dialog", _move_pickers_and_ok)
        harness.fire_menu_event(context.frame, "mi_set_start_time")

        status_text = context.frame.GetStatusBar().GetStatusText()
        assert opened == [ids.SET_START_DLG]
        assert picked["prefill_date"] == (2026, 9, 20)
        assert picked["prefill_time"] == (10, 0)
        assert engine.state is RideStatus.RUNNING
        assert status_text == "Start time set"
        assert engine.events[-1].action == "set_start_time"
        assert engine.events[-1].payload["actual_start"] == "2026-09-21T09:55:00"
        assert engine.events[-1].payload["previous_start"] == "2026-09-20T10:00:00"
    finally:
        harness.close_window(context.frame)


def test_mi_set_start_time_reopened_posts_the_engine_refusal_notice(
    xrc_resource: object,
    wx_app: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set Start Time… over a REOPENED ride surfaces the engine refusal.

    The §15 enablement lights the row at REOPENED too, but
    ``engine.set_start_time`` is RUNNING-only: the confirmed dialog
    must surface the engine's refusal as a notice and change nothing,
    never crash or silently no-op.
    """
    context, engine = _build_live_console(xrc_resource, wx_app, reopened=True)
    events_before = len(engine.events)
    opened: list[str] = []

    def _auto_ok(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001
        opened.append(dialog.GetName())
        return wx.ID_OK

    try:
        monkeypatch.setattr(dialogs, "run_dialog", _auto_ok)
        harness.fire_menu_event(context.frame, "mi_set_start_time")

        status_text = context.frame.GetStatusBar().GetStatusText()
        assert opened == [ids.SET_START_DLG]
        assert engine.state is RideStatus.REOPENED
        assert len(engine.events) == events_before
        # The engine words its own refusal with the lowercase state
        # (ride.py's "cannot set start time from reopened" -- the same
        # spelling test_ride_library_live pins for the reopen refusal);
        # the status bar posts that message verbatim.
        assert status_text == "Correction refused: cannot set start time from reopened"
    finally:
        harness.close_window(context.frame)


def test_mi_start_ride_incomplete_setup_posts_the_gate_and_stays_draft(
    xrc_resource: object,
    wx_app: object,
) -> None:
    """S2: Start Ride over a roster with a blank minimum field refuses.

    A DRAFT ride with one rider but a blank venue trips the start
    gate's incomplete-setup arm (R-79 / spec §15's start-gate row):
    ``mi_start_ride`` posts "Cannot start: ride setup is incomplete:
    …" naming the missing field and the ride stays DRAFT -- the
    empty-roster arm's sibling through the UI.
    """
    context, engine = _build_live_console(xrc_resource, wx_app, venue="", started=False)

    try:
        harness.fire_menu_event(context.frame, "mi_start_ride")

        status_text = context.frame.GetStatusBar().GetStatusText()
        status_label = harness.find_control(context.frame, ids.RIDE_STATUS_LBL)
        assert engine.state is RideStatus.DRAFT
        assert status_text == "Cannot start: ride setup is incomplete: venue is required"
        assert status_label.GetLabelText() == "DRAFT"
        assert not any(event.action == "start" for event in engine.events)
    finally:
        harness.close_window(context.frame)


def test_settings_backup_now_btn_writes_a_backup_and_posts_its_path(  # noqa: PLR0913, PLR0917 -- (xrc_resource, wx_app, tmp_path, monkeypatch): the four fixture seams
    xrc_resource: object,
    wx_app: object,
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S5: the Settings dialog's ``backup_now_btn`` runs the R-54 flow.

    ux-polish wired the dialog's button to the same
    ``_handle_backup_database`` action File ▸ Back Up Database… fires
    (through ``_decorate``'s ``on_backup_now`` seam); only the menu
    row was exercised before. Clicking the button while the dialog is
    open (the patched ``run_dialog`` drives the click before closing)
    writes one timestamped backup into ``<db>.backups/`` and posts the
    written path on the main frame's status bar.
    """
    db_path = Path(str(tmp_path)) / "rides.db"
    store = Store.open(db_path)
    context, _engine = _build_live_console(xrc_resource, wx_app, store=store)
    opened: list[str] = []

    def _click_backup_then_cancel(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001
        opened.append(dialog.GetName())
        harness.click(dialog, ids.BACKUP_NOW_BTN)
        return wx.ID_CANCEL

    try:
        monkeypatch.setattr(dialogs, "run_dialog", _click_backup_then_cancel)
        harness.fire_menu_event(context.frame, "wxID_PREFERENCES")

        status_text = context.frame.GetStatusBar().GetStatusText()
        backup_dir = Path(f"{db_path}.backups")
        backups = sorted(backup_dir.glob("*.db")) if backup_dir.is_dir() else []

        assert opened == [ids.SETTINGS_DLG]
        assert len(backups) == 1
        assert status_text == f"Backed up database to {backups[-1]}"
    finally:
        store.close()
        harness.close_window(context.frame)
