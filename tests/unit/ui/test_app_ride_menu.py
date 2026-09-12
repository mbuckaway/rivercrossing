# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the Ride menu's Edit Ride… / Clear Ride… routes.

D2 and D3 wire two new ``commands.ROUTE_TABLE`` rows to real actions:

- **Edit Ride…** opens ``ride_setup_dlg`` PRELOADED with the live
  ride's config (:func:`rivercrossing.ui.app._decorate_edit_ride`) and,
  on submit, writes the edited config back onto the live engine and
  the store row (:func:`rivercrossing.ui.app._apply_edited_ride`).
- **Clear Ride…** confirms through the native danger dialog
  (``std_dialogs.show_danger``, stubbed here -- T-10: wx is the GUI
  I/O boundary) and then resets the ride to a fresh empty DRAFT
  (:func:`rivercrossing.ui.app._handle_clear_ride_route`), reusing the
  library-Open console switch.

Ride data runs against a real ``Store`` over a ``tmp_path`` file; the
console is a recording fake and no wx window is ever constructed.
"""

import sqlite3
from dataclasses import replace
from typing import TYPE_CHECKING

import wx

from conftest import gorba_config
from rivercrossing.ride import Event, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.store import Store
from rivercrossing.ui import app as app_module
from rivercrossing.ui import std_dialogs
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.presenters.data_source import EngineDataSource
from rivercrossing.ui.views import ride_setup as ride_setup_module

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_START = "2026-09-20T10:00:00"
_CROSSING = {
    "plate": "12",
    "entry_id": "12",
    "lap": 1,
    "crossed_at": "2026-09-20T10:02:00",
}


class _FakeFrame:
    """Record status-bar notices; no wx window ever exists."""

    def __init__(self) -> None:
        """Start with an empty notice log."""
        self.notices: list[str] = []

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record one status-bar notice."""
        self.notices.append(text)


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

    def show_ride_header(self, **fields: object) -> None:
        """Record the rendered ride-identity header (C1)."""
        self.calls.append(("show_ride_header", fields))

    def set_state(self, status: RideStatus, *, stopped: bool = False) -> None:
        """Record the rendered lifecycle state and stop guard (W6)."""
        self.calls.append(("set_state", (status, stopped)))

    def show_feed(self, rows: list[object]) -> None:
        """Record the number of rendered feed rows."""
        self.calls.append(("show_feed", len(rows)))

    def show_counters(self, counters: object) -> None:
        """Record the rendered counters."""
        self.calls.append(("show_counters", counters))

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Record the R-11 teams-chip visibility push."""
        self.calls.append(("set_team_ui_visible", visible))

    def focus_entry(self) -> None:
        """Record the focus request."""
        self.calls.append(("focus_entry", None))


class _ClearFailsStore:
    """A store whose clear_ride refuses like a locked database."""

    def clear_ride(self, ride_id: int) -> None:  # noqa: ARG002 -- the fake only raises
        """Raise the store error the handler must surface."""
        raise sqlite3.OperationalError("database is locked")


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


def test_handle_clear_ride_route_given_a_confirmed_danger_clears_the_ride(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D3: a confirmed Clear wipes the ride and swaps the console."""
    db_path = tmp_path / "rides.db"
    ride_id = _stage_running_ride(db_path)
    store = Store.open(db_path)
    try:
        frame = _FakeFrame()
        view = _FakeConsoleView()
        context = _context(store=store, view=view, frame=frame)
        context.active_ride_id = ride_id
        engine = store.load_engine(ride_id, context.roster)
        _live_console(context, engine, view)
        calls = _stub_danger(monkeypatch, result=wx.ID_OK)
        view.calls.clear()

        app_module._handle_clear_ride_route(context)

        assert store.audit_rows(ride_id) == []
        assert store.roster_for(ride_id).entries == ()
        assert ("set_state", (RideStatus.DRAFT, False)) in view.calls
        assert (
            "show_ride_header",
            {
                "name": "GORBA EPIC 2026",
                "logo": None,
                "event_date": engine.config.event_date,
                "planned_start": engine.config.planned_start,
                "entry_mode": engine.config.entry_mode,
            },
        ) in view.calls
    finally:
        store.close()
    parent, title, message, ok_label, cancel_label = calls[0][0]
    assert (parent, title, ok_label, cancel_label) == (frame, "Clear Ride", "Clear Ride", "Cancel")
    assert "GORBA EPIC 2026" in message


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
        _live_console(context, store.load_engine(ride_id, context.roster), view)
        _stub_danger(monkeypatch, result=wx.ID_CANCEL)
        view.calls.clear()

        app_module._handle_clear_ride_route(context)

        # audit_rows is newest-first: both rows survive, in ride order.
        assert [row.action for row in store.audit_rows(ride_id)] == [
            "record_crossing",
            "start",
        ]
        assert context.active_ride_id == ride_id
        assert view.calls == []
    finally:
        store.close()


def test_handle_clear_ride_route_given_a_refused_clear_posts_the_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A locked database is a notice, never a swallowed raise."""
    frame = _FakeFrame()
    view = _FakeConsoleView()
    context = _context(store=_ClearFailsStore(), view=view, frame=frame)
    context.active_ride_id = 7
    engine, source = app_module._build_console_engine(context.roster)
    context.presenter = ConsolePresenter(view, engine=engine, source=source)
    _stub_danger(monkeypatch, result=wx.ID_OK)
    view.calls.clear()

    app_module._handle_clear_ride_route(context)

    assert frame.notices == ["Could not clear ride: database is locked"]
    assert view.calls == []


def test_handle_clear_ride_route_without_a_store_resets_the_bootstrap_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D3: the store-less console clears to a fresh DRAFT ride."""
    view = _FakeConsoleView()
    context = _context(store=None, view=view)
    engine, source = app_module._build_console_engine(context.roster)
    context.presenter = ConsolePresenter(view, engine=engine, source=source)
    _stub_danger(monkeypatch, result=wx.ID_OK)
    view.calls.clear()

    app_module._handle_clear_ride_route(context)

    assert context.presenter is not None
    assert context.presenter.engine.state is RideStatus.DRAFT
    assert context.presenter.engine.crossings == ()
    assert ("set_state", (RideStatus.DRAFT, False)) in view.calls


def test_handle_clear_ride_route_without_a_presenter_posts_the_notice() -> None:
    """D3: no live ride is a notice, not a crash."""
    context = _context(store=None)

    app_module._handle_clear_ride_route(context)

    assert context.frame.notices == ["Clear Ride — no ride open"]


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
