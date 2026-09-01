# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for app.py's New Ride console-switch wiring (E9.1.4).

Phase 1 of EPIC 9 persisted a New Ride (``Store.create_ride`` +
``Store.save_roster``) but left the console on the bootstrap engine:
the setup dialog closed and the console kept running the non-store
engine, so crossings typed immediately after hit the empty engine
instead of the new ride. This module proves the two seams that fix
it, headless with a real Store and a recording fake console view:

- :func:`rivercrossing.ui.app._persist_created_ride` creates the
  ride row, persists the roster, marks the ride active on the open
  session, and schedules the console switch.
- :func:`rivercrossing.ui.app._switch_console_to_ride` loads the ride
  from the store, renders its name and DRAFT state onto the view, and
  wires the store's append as the engine's event sink.

The wx boundary is the one mocked thing: ``require_wx`` is replaced
with a recorder so the deferred ``wx.CallAfter`` switch is observed
without constructing any GUI (T-10: wx is the GUI I/O boundary).
"""

from datetime import date, datetime
from typing import TYPE_CHECKING

from rivercrossing.ride import Event, RideConfig, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.store import Store
from rivercrossing.ui import app as app_module
from rivercrossing.ui.presenters.console import ConsolePresenter

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _FakeConsoleView:
    """Record every render call a console switch makes."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, object]] = []

    def set_presenter(self, presenter: object) -> None:
        """Record the swapped presenter."""
        self.calls.append(("set_presenter", presenter))

    def show_ride_name(self, name: str) -> None:
        """Record the rendered ride name."""
        self.calls.append(("show_ride_name", name))

    def set_state(self, status: RideStatus) -> None:
        """Record the rendered lifecycle state."""
        self.calls.append(("set_state", status))

    def show_feed(self, rows: list[object]) -> None:
        """Record the number of rendered feed rows."""
        self.calls.append(("show_feed", len(rows)))

    def show_counters(self, counters: object) -> None:
        """Record the rendered counters."""
        self.calls.append(("show_counters", counters))

    def focus_entry(self) -> None:
        """Record the focus request."""
        self.calls.append(("focus_entry", None))


class _FakeWx:
    """Record every ``CallAfter`` schedule without constructing GUI."""

    def __init__(self) -> None:
        """Start with an empty schedule log."""
        self.calls: list[tuple[object, tuple[object, ...]]] = []

    def CallAfter(self, callable_: object, *args: object) -> None:  # noqa: N802 -- wx API name
        """Record one deferred call."""
        self.calls.append((callable_, args))


def _config() -> RideConfig:
    """Build the store ride config these tests persist."""
    return RideConfig(
        name="GORBA EPIC 2026",
        event_date=date(2026, 9, 20),
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive local, Store's own contract
        planned_duration_s=21600,
        min_lap_s=1080,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )


def _roster() -> Roster:
    """Build the one-entry roster the switch must rebuild."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(name="Sam Ellis", plate="12")
    return roster


def _context(*, store: Store, view: _FakeConsoleView, roster: Roster) -> app_module._RouteContext:
    """Build a route context carrying *store*, *view* and *roster*."""
    return app_module._RouteContext(
        frame=object(),
        resource=None,
        roster=roster,
        app=None,
        theme_controller=None,
        store=store,
        console_view=view,
    )


def test_persist_created_ride_sets_active_and_schedules_console_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """New Ride submit persists, marks active, schedules the switch."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        view = _FakeConsoleView()
        context = _context(store=store, view=view, roster=_roster())
        fake_wx = _FakeWx()
        monkeypatch.setattr(app_module, "require_wx", lambda: fake_wx)

        app_module._persist_created_ride(context, _config())

        rides = store.rides()
        assert [ride.name for ride in rides] == ["GORBA EPIC 2026"]
        ride_id = rides[0].id
        assert [entry.plate for entry in store.roster_for(ride_id).entries] == ["12"]
        session = store._conn.execute(
            "SELECT active_ride_id FROM app_session ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert session["active_ride_id"] == ride_id
        assert fake_wx.calls == [(app_module._switch_console_to_ride, (context, ride_id))]
    finally:
        store.close()


def test_switch_console_to_ride_renders_name_and_draft_and_wires_append(
    tmp_path: Path,
) -> None:
    """The switch loads the ride, renders name/DRAFT, wires the sink."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
        store.save_roster(ride_id, _roster())
        view = _FakeConsoleView()
        context = _context(store=store, view=view, roster=_roster())

        app_module._switch_console_to_ride(context, ride_id)

        assert context.active_ride_id == ride_id
        assert [entry.plate for entry in context.roster.entries] == ["12"]
        assert [name for name, _arg in view.calls] == [
            "set_presenter",
            "show_ride_name",
            "set_state",
            "show_feed",
            "show_counters",
            "focus_entry",
        ]
        swapped = next(arg for name, arg in view.calls if name == "set_presenter")
        assert isinstance(swapped, ConsolePresenter)
        assert swapped.engine.state is RideStatus.DRAFT
        assert ("show_ride_name", "GORBA EPIC 2026") in view.calls
        assert ("set_state", RideStatus.DRAFT) in view.calls
        # The engine's event sink is wired to the store for this ride.
        swapped.engine.on_event(
            Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        )
        assert [row.action for row in store.audit_rows(ride_id)] == ["start"]
    finally:
        store.close()
