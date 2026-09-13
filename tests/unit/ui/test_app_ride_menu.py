# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the Ride menu's Edit Ride… / Clear Ride… routes.

D2 and D3 wire two new ``commands.ROUTE_TABLE`` rows to real actions:

- **Edit Ride…** opens ``ride_setup_dlg`` PRELOADED with the live
  ride's config (:func:`rivercrossing.ui.app._decorate_edit_ride`) and,
  on submit, writes the edited config back onto the live engine and
  the store row (:func:`rivercrossing.ui.app._apply_edited_ride`).
- **Clear Ride…** confirms through the native danger dialog
  (``std_dialogs.show_danger``, stubbed here -- T-10: wx is the GUI
  I/O boundary) and then removes the ride from the screen only
  (:func:`rivercrossing.ui.app._handle_clear_ride_route`): the store
  is never written to, the presenter is unthreaded, and the console
  renders its no-ride empty state.

Ride data runs against a real ``Store`` over a ``tmp_path`` file; the
console is a recording fake and no wx window is ever constructed.
"""

import sqlite3
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
import wx

from conftest import gorba_config
from rivercrossing.cards import Shoe
from rivercrossing.ride import Event, RideEngine, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.store import Store
from rivercrossing.ui import app as app_module
from rivercrossing.ui import commands, ids, std_dialogs
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.presenters.data_source import EngineDataSource
from rivercrossing.ui.views import ride_setup as ride_setup_module
from rivercrossing.ui.views.main_frame import MainFrame

if TYPE_CHECKING:
    from pathlib import Path

_START = "2026-09-20T10:00:00"
_CROSSING = {
    "plate": "12",
    "entry_id": "12",
    "lap": 1,
    "crossed_at": "2026-09-20T10:02:00",
}


class _FakeFrame:
    """Record status-bar notices; no wx window ever exists."""

    def __init__(self, menubar: object = None) -> None:
        """Start with an empty notice log; carry *menubar* verbatim."""
        self.notices: list[str] = []
        self._menubar = menubar

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record one status-bar notice."""
        self.notices.append(text)

    def GetMenuBar(self) -> object:  # noqa: N802 -- wx API name the SUT calls
        """Answer the threaded menubar (``None`` by default)."""
        return self._menubar


class _RecordingMenuItem:
    """A menu item that records the last ``Enable`` verdict."""

    def __init__(self) -> None:
        """Start with no recorded verdict."""
        self.enabled: bool | None = None

    def Enable(self, enabled: bool) -> None:  # noqa: N802, FBT001 -- wx API name
        """Record *enabled* as the item's verdict."""
        self.enabled = enabled


class _RecordingMenuBar:
    """A menubar carrying exactly the observed frozen item names.

    The same recording double ``test_app_exports.py`` drives, over the
    real ``wx.xrc.XRCID`` id space ``menu_state.apply_to_menubar``
    walks -- the menu binder's wx touch is the XRCID lookup and
    ``MenuItem.Enable``, both faked here.
    """

    def __init__(self, names: tuple[str, ...]) -> None:
        """Build one recording item per frozen *names* entry."""
        import wx.xrc  # noqa: PLC0415 -- the real id space the binder walks

        self._names = {wx.xrc.XRCID(name): name for name in names}
        self.items = {name: _RecordingMenuItem() for name in names}

    def FindItem(  # noqa: N802 -- wx API name
        self, real_id: int
    ) -> tuple[_RecordingMenuItem | None, None]:
        """Return the item for *real_id*, or a miss."""
        name = self._names.get(real_id)
        return (None, None) if name is None else (self.items[name], None)


_RIDE_LIFECYCLE_MENU_IDS = (ids.MI_NEW_RIDE, ids.MI_EDIT_RIDE)


class _EnginePresenter:
    """A presenter stand-in exposing the engine the binder reads."""

    def __init__(self, engine: object) -> None:
        """Store the engine ``app._menu_ride_state`` projects."""
        self.engine = engine


def _menu_engine() -> RideEngine:
    """Build a DRAFT engine over the one-entry MIXED roster."""
    config = gorba_config()
    return RideEngine(
        config=config,
        shoe=Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=7),
        clock=lambda: config.planned_start,
        roster=_roster(),
    )


class _FakeWindow:
    """Record the dialog title the decorator applies; nothing else."""

    def __init__(self) -> None:
        """Start with no recorded titles."""
        self.titles: list[str] = []

    def SetTitle(self, title: str) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the applied window title."""
        self.titles.append(title)


class _FakeConsoleView:
    """Record every render call a console swap makes."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, object]] = []

    def set_presenter(self, presenter: object) -> None:
        """Record the swapped presenter."""
        self.calls.append(("set_presenter", presenter))

    def clear_presenter(self) -> None:
        """Record the D3 presenter detach."""
        self.calls.append(("clear_presenter", None))

    def show_no_ride(self) -> None:
        """Record the W1 no-ride empty-state render."""
        self.calls.append(("show_no_ride", None))

    def show_ride_header(self, **fields: object) -> None:
        """Record the rendered ride-identity header (C1)."""
        self.calls.append(("show_ride_header", fields))

    def set_state(self, status: RideStatus, *, stopped: bool = False) -> None:
        """Record the rendered lifecycle state and stop guard (W6)."""
        self.calls.append(("set_state", (status, stopped)))

    def show_feed(self, rows: list[object]) -> None:
        """Record the number of rendered feed rows."""
        self.calls.append(("show_feed", len(rows)))

    def show_flagged(self, rows: list[object]) -> None:
        """Record the number of rendered review-tab rows (WS-H).

        The presenter's own ``refresh_feed`` feeds both lists, so a
        console double of the swap path carries both channels.
        """
        self.calls.append(("show_flagged", len(rows)))

    def show_counters(self, counters: object) -> None:
        """Record the rendered counters."""
        self.calls.append(("show_counters", counters))

    def show_current_lap(self, lap: int) -> None:
        """Record the rendered Current Lap reading (Phase 6)."""
        self.calls.append(("show_current_lap", lap))

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Record the R-11 teams-chip visibility push."""
        self.calls.append(("set_team_ui_visible", visible))

    def focus_entry(self) -> None:
        """Record the focus request."""
        self.calls.append(("focus_entry", None))


class _FakeTimer:
    """Record clear_presenter's stop; no wx timer exists."""

    def __init__(self) -> None:
        """Start running, as ``wire_console`` leaves the real timer."""
        self.stopped = False

    def Stop(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the stop."""
        self.stopped = True


class _UpdateFailsStore:
    """A store whose update_ride_config refuses the write."""

    def update_ride_config(self, ride_id: int, config: object) -> None:
        """Raise the store error the handler must surface."""
        _ = (ride_id, config)  # the fake never reads them; it only raises
        raise sqlite3.OperationalError("database is locked")


def _roster() -> Roster:
    """Build the one-entry MIXED roster every staged ride saves."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="12")
    return roster


def _context(
    *,
    store: object = None,
    view: _FakeConsoleView | None = None,
    frame: _FakeFrame | None = None,
) -> app_module._RouteContext:
    """Build a route context over *store* with fake frame/view."""
    return app_module._RouteContext(
        frame=frame if frame is not None else _FakeFrame(),
        resource=None,
        roster=_roster(),
        app=None,
        theme_controller=None,
        store=store,  # type: ignore[arg-type]
        console_view=view,
    )


def _live_console(
    context: app_module._RouteContext,
    engine: object,
    view: _FakeConsoleView,
) -> ConsolePresenter:
    """Thread a real presenter over *engine* into *context*."""
    presenter = ConsolePresenter(
        view, engine=engine, source=EngineDataSource(engine, context.roster)
    )  # type: ignore[arg-type]
    context.presenter = presenter
    return presenter


def _stage_running_ride(db_path: Path) -> int:
    """Stage a RUNNING ride with one crossing and the active marker."""
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(gorba_config())
        store.save_roster(ride_id, _roster())
        store.append(ride_id, Event(action="start", payload={"actual_start": _START}))
        store.append(ride_id, Event(action="record_crossing", payload=dict(_CROSSING)))
        store.set_active_ride(ride_id)
    finally:
        store.close()
    return ride_id


def _stub_danger(
    monkeypatch: pytest.MonkeyPatch, *, result: int
) -> list[tuple[tuple[object, ...], dict[str, object]]]:
    """Stub the native danger dialog and record how it was called."""
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _show(*args: object, **kwargs: object) -> int:
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(std_dialogs, "show_danger", _show)
    return calls


# ------------------------------------------------------ Clear Ride…


def test_handle_clear_ride_route_given_a_confirmed_danger_leaves_the_store_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D3: a confirmed Clear removes the ride from the screen only."""
    db_path = tmp_path / "rides.db"
    ride_id = _stage_running_ride(db_path)
    store = Store.open(db_path)
    try:
        frame = _FakeFrame()
        view = _FakeConsoleView()
        context = _context(store=store, view=view, frame=frame)
        app_module._switch_console_to_ride(context, ride_id)
        engine = context.presenter.engine  # type: ignore[union-attr] -- the swap above set it
        context.detail_plate = "12"
        context.html_export_path = tmp_path / "results.html"
        context.pdf_export_path = tmp_path / "results.pdf"
        context.export_watermark = 2
        calls = _stub_danger(monkeypatch, result=wx.ID_OK)
        view.calls.clear()

        app_module._handle_clear_ride_route(context)

        assert [row.action for row in store.audit_rows(ride_id)] == [
            "record_crossing",
            "start",
        ]
        assert [entry.plate for entry in store.roster_for(ride_id).entries] == ["12"]
        assert engine.on_event is None
        assert context.presenter is None
        assert context.active_ride_id is None
        assert context.detail_plate is None
        assert context.html_export_path is None
        assert context.pdf_export_path is None
        assert context.export_watermark is None
        assert context.roster.entries == ()
        assert context.roster.entry_mode is EntryMode.MIXED
        assert context.roster.plate_model is PlateModel.RIDER_POOLED
        assert context.roster.max_team_size == app_module._SEEDED_MAX_TEAM_SIZE
        assert context.roster.team_logo_seed == app_module._SEEDED_TEAM_LOGO_SEED
        assert ("clear_presenter", None) in view.calls
        assert ("show_no_ride", None) in view.calls
        assert frame.notices == ["Ride removed from the screen"]
    finally:
        store.close()
    parent, title, message, ok_label, cancel_label = calls[0][0]
    assert (parent, title, ok_label, cancel_label) == (frame, "Clear Ride", "Clear Ride", "Cancel")
    assert "GORBA EPIC 2026" in message
    assert "from the screen only" in message
    assert "stay saved in the database" in message


def test_handle_clear_ride_route_given_a_cancelled_danger_leaves_the_ride_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D3: Cancel is the safe path -- nothing is written anywhere."""
    db_path = tmp_path / "rides.db"
    ride_id = _stage_running_ride(db_path)
    store = Store.open(db_path)
    try:
        view = _FakeConsoleView()
        context = _context(store=store, view=view)
        context.active_ride_id = ride_id
        presenter = _live_console(context, store.load_engine(ride_id, context.roster), view)
        _stub_danger(monkeypatch, result=wx.ID_CANCEL)
        view.calls.clear()

        app_module._handle_clear_ride_route(context)

        # audit_rows is newest-first: both rows survive, in ride order.
        assert [row.action for row in store.audit_rows(ride_id)] == [
            "record_crossing",
            "start",
        ]
        assert context.active_ride_id == ride_id
        assert context.presenter is presenter
        assert view.calls == []
    finally:
        store.close()


def test_handle_clear_ride_route_without_a_store_clears_the_console_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D3: the store-less console clears to the no-ride empty state."""
    view = _FakeConsoleView()
    context = _context(store=None, view=view)
    engine, _source = app_module._build_console_engine(context.roster)
    _live_console(context, engine, view)
    context.detail_plate = "12"
    _stub_danger(monkeypatch, result=wx.ID_OK)
    view.calls.clear()

    app_module._handle_clear_ride_route(context)

    assert context.presenter is None
    assert context.active_ride_id is None
    assert context.detail_plate is None
    assert context.roster.entries == ()
    assert ("clear_presenter", None) in view.calls
    assert ("show_no_ride", None) in view.calls


def test_handle_clear_ride_route_given_no_console_view_clears_without_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D3: a console-less context still clears, no crash."""
    view = _FakeConsoleView()
    context = _context(store=None)
    engine, _source = app_module._build_console_engine(context.roster)
    _live_console(context, engine, view)
    _stub_danger(monkeypatch, result=wx.ID_OK)
    view.calls.clear()

    app_module._handle_clear_ride_route(context)

    assert context.presenter is None
    assert view.calls == []


def test_handle_clear_ride_route_without_a_presenter_posts_the_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D3: no live ride is a notice, not a crash."""
    context = _context(store=None)
    calls = _stub_danger(monkeypatch, result=wx.ID_OK)

    app_module._handle_clear_ride_route(context)

    assert context.frame.notices == ["Clear Ride — no ride open"]
    assert calls == []


# The route's collaborator, built headless with the ``object.__new__``
# precedent (test_dialogs_positioning.py): no wx window is constructed.


def test_clear_presenter_given_a_wired_timer_stops_it_and_unbinds_the_callbacks() -> None:
    """D3: a cleared console holds no reference to its old presenter."""
    console = object.__new__(MainFrame)
    timer = _FakeTimer()
    console._tick_timer = timer
    console._presenter = object()
    console._on_submit = object()

    console.clear_presenter()

    assert timer.stopped is True
    assert console._presenter is None
    assert console._on_submit is None


def test_clear_presenter_without_a_wired_timer_unbinds_only_the_callbacks() -> None:
    """D3: the never-wired console clears without touching a timer."""
    console = object.__new__(MainFrame)
    console._tick_timer = None
    console._presenter = object()
    console._on_submit = object()

    console.clear_presenter()

    assert console._tick_timer is None
    assert console._presenter is None
    assert console._on_submit is None


# ----------------------------------- D1: the New Ride… enablement
#
# ``_apply_menu_state`` is the live E1.4.2 binder: it computes one
# ``commands.RideState`` from the engine (``_menu_ride_state``)
# and applies §15's rules to the real menubar. D1 makes New Ride… the
# one row that is enabled only while NO ride is loaded -- the setup
# dialog is what opens a ride -- so the binder's two branches are pinned
# here against a real engine, not only in ``test_menu_state.py``.


def test_apply_menu_state_given_a_ride_loaded_disables_new_ride_and_enables_edit_ride() -> None:
    """D1/D2: a loaded ride turns New Ride… off and Edit Ride… on."""
    menubar = _RecordingMenuBar(_RIDE_LIFECYCLE_MENU_IDS)
    context = _context(frame=_FakeFrame(menubar))
    context.presenter = _EnginePresenter(_menu_engine())

    app_module._apply_menu_state(context, RideStatus.DRAFT)

    assert menubar.items[ids.MI_NEW_RIDE].enabled is False
    assert menubar.items[ids.MI_EDIT_RIDE].enabled is True


def test_apply_menu_state_given_no_ride_enables_new_ride_and_disables_edit_ride() -> None:
    """D1/D2: with no ride loaded only New Ride… is available."""
    menubar = _RecordingMenuBar(_RIDE_LIFECYCLE_MENU_IDS)
    context = _context(frame=_FakeFrame(menubar))

    app_module._apply_menu_state(context, RideStatus.DRAFT)

    assert menubar.items[ids.MI_NEW_RIDE].enabled is True
    assert menubar.items[ids.MI_EDIT_RIDE].enabled is False


# ------------------------------------------------------- Edit Ride…


def _patch_ride_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    """Swap RideSetup for a recorder so the decorator is testable."""
    captured: dict[str, object] = {}

    def _fake_ride_setup(dialog: object, **kwargs: object) -> None:
        captured["dialog"] = dialog
        captured.update(kwargs)

    monkeypatch.setattr(ride_setup_module, "RideSetup", _fake_ride_setup)
    return captured


def test_decorate_edit_ride_titles_the_dialog_and_preloads_the_live_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D2: Edit Ride opens the setup dialog on the ride's own config."""
    captured = _patch_ride_setup(monkeypatch)
    view = _FakeConsoleView()
    context = _context(store=None, view=view)
    engine, source = app_module._build_console_engine(context.roster)
    context.presenter = ConsolePresenter(view, engine=engine, source=source)
    window = _FakeWindow()

    app_module._decorate_edit_ride(context, window)

    assert window.titles == ["Edit Ride"]
    assert captured["config"] == engine.config
    assert captured["roster"] == context.roster


def test_decorate_edit_ride_wires_a_submit_to_the_edit_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D2: a committed Edit Ride saves back onto the live ride."""
    captured = _patch_ride_setup(monkeypatch)
    applied: list[tuple[object, object]] = []
    monkeypatch.setattr(
        app_module, "_apply_edited_ride", lambda ctx, config: applied.append((ctx, config))
    )
    view = _FakeConsoleView()
    context = _context(store=None, view=view)
    engine, source = app_module._build_console_engine(context.roster)
    context.presenter = ConsolePresenter(view, engine=engine, source=source)
    edited = replace(engine.config, name="Renamed")

    app_module._decorate_edit_ride(context, _FakeWindow())
    on_submitted = captured["on_submitted"]
    assert callable(on_submitted)
    on_submitted(edited)

    assert applied == [(context, edited)]


def test_decorate_edit_ride_without_a_presenter_posts_the_notice() -> None:
    """D2: the guard posts the engine-less routes' own notice."""
    context = _context(store=None)
    window = _FakeWindow()

    app_module._decorate_edit_ride(context, window)

    assert context.frame.notices == ["Edit Ride — no ride open"]
    assert window.titles == []


def test_apply_edited_ride_rewrites_the_stored_ride_and_the_live_engine(
    tmp_path: Path,
) -> None:
    """D2: a confirmed edit persists and re-renders the header."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(gorba_config())
        view = _FakeConsoleView()
        context = _context(store=store, view=view)
        context.active_ride_id = ride_id
        engine = store.load_engine(ride_id, context.roster)
        _live_console(context, engine, view)
        edited = replace(gorba_config(), name="Renamed", venue="New Venue")
        view.calls.clear()

        app_module._apply_edited_ride(context, edited)

        assert context.presenter is not None
        assert context.presenter.engine.config.name == "Renamed"
        assert store.load_engine(ride_id, context.roster).config.venue == "New Venue"
        assert (
            "show_ride_header",
            {
                "name": "Renamed",
                "logo": None,
                "event_date": edited.event_date,
                "planned_start": edited.planned_start,
                "entry_mode": edited.entry_mode,
                "venue": "New Venue",
                "organizer": edited.organizer,
                "scorer": edited.scorer,
                "lap_km": edited.lap_km,
            },
        ) in view.calls
    finally:
        store.close()
    assert context.frame.notices == ["Ride settings saved"]


def test_apply_edited_ride_given_no_store_keeps_the_edit_in_memory() -> None:
    """D2: the store-less console still edits its live ride."""
    view = _FakeConsoleView()
    context = _context(store=None, view=view)
    engine, source = app_module._build_console_engine(context.roster)
    context.presenter = ConsolePresenter(view, engine=engine, source=source)
    edited = replace(engine.config, name="Renamed")

    app_module._apply_edited_ride(context, edited)

    assert context.presenter is not None
    assert context.presenter.engine.config == edited


def test_apply_edited_ride_given_a_refused_write_keeps_the_old_config() -> None:
    """A locked database never leaves the live ride half-edited."""
    view = _FakeConsoleView()
    context = _context(store=_UpdateFailsStore(), view=view)
    context.active_ride_id = 7
    engine, source = app_module._build_console_engine(context.roster)
    presenter = ConsolePresenter(view, engine=engine, source=source)
    context.presenter = presenter

    app_module._apply_edited_ride(context, replace(engine.config, name="Renamed"))

    assert context.frame.notices == ["Could not save ride: database is locked"]
    assert presenter.engine.config.name == "GORBA EPIC 2026"


def test_apply_edited_ride_without_a_presenter_posts_the_notice() -> None:
    """D2: no live ride is a notice, not a crash."""
    context = _context(store=None)

    app_module._apply_edited_ride(context, gorba_config())

    assert context.frame.notices == ["Edit Ride — no ride open"]


# ------------------------- Ride ▸/Cards ▸ commands: fire-time presenter
#
# ``_bind_routes`` binds every route once at bootstrap, while
# ``context.presenter`` is still ``None``; a ride is opened later by
# mutating the same context in place (E5.4.1's console swap). The three
# COMMAND routes that act on the live console -- Ride ▸ Start Ride, Ride
# ▸ Stop Ride…, Cards ▸ Undo Last Crossing -- must therefore resolve
# ``context.presenter`` when the menu item fires, never when the handler
# is built: a bind-time snapshot leaves each row stuck on its fallback.
# These tests build the handler first (presenter ``None``, the bootstrap
# state), then set the presenter, then fire.


class _RecordingRidePresenter:
    """Record the three ride-command calls the menu routes fire."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[str] = []

    def on_start(self) -> None:
        """Record the Ride ▸ Start Ride dispatch."""
        self.calls.append("on_start")

    def on_stop_requested(self) -> None:
        """Record the Ride ▸ Stop Ride… dispatch."""
        self.calls.append("on_stop_requested")

    def on_undo(self) -> None:
        """Record the Cards ▸ Undo Last Crossing dispatch."""
        self.calls.append("on_undo")


_RIDE_COMMAND_ROUTES = (
    pytest.param(ids.MI_START_RIDE, "on_start", id="start_ride"),
    pytest.param(ids.MI_STOP_RIDE, "on_stop_requested", id="stop_ride"),
    pytest.param(ids.MI_UNDO_CROSSING, "on_undo", id="undo_last_crossing"),
)

_RIDE_COMMAND_NOTICES = (
    pytest.param(ids.MI_START_RIDE, "Start Ride — no ride open", id="start_ride"),
    pytest.param(ids.MI_STOP_RIDE, "Stop Ride… — not yet implemented", id="stop_ride"),
    pytest.param(
        ids.MI_UNDO_CROSSING,
        "Undo Last Crossing — not yet implemented",
        id="undo_last_crossing",
    ),
)


@pytest.mark.parametrize(("item_id", "expected_call"), _RIDE_COMMAND_ROUTES)
def test_make_route_handler_given_a_ride_opened_after_binding_fires_the_presenter(
    item_id: str, expected_call: str
) -> None:
    """Plan §7: the bootstrap-bound handler reaches the opened ride."""
    route = commands.route_for_id(item_id)
    context = _context(store=None)
    bound = app_module._make_route_handler(context, route)
    presenter = _RecordingRidePresenter()
    context.presenter = presenter  # type: ignore[assignment]

    bound(None)

    assert presenter.calls == [expected_call]
    assert context.frame.notices == []


@pytest.mark.parametrize(("item_id", "expected_notice"), _RIDE_COMMAND_NOTICES)
def test_make_route_handler_given_no_ride_posts_the_route_notice(
    item_id: str, expected_notice: str
) -> None:
    """Plan §7: with no ride open the route keeps its own notice."""
    route = commands.route_for_id(item_id)
    context = _context(store=None)

    bound = app_module._make_route_handler(context, route)
    bound(None)

    assert context.frame.notices == [expected_notice]
