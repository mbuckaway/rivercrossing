# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for app.py's New Ride console-switch wiring (E9.1.4).

Phase 1 of EPIC 9 persisted a New Ride (``Store.create_ride`` +
``Store.save_roster``) but left the console on the bootstrap engine:
the setup dialog closed and the console kept running the non-store
engine, so crossings typed immediately after hit the empty engine
instead of the new ride. This module proves the two seams that fix
it, headless with a real Store and a recording fake console view:

- :func:`rivercrossing.ui.app._persist_created_ride` creates the
  ride row, persists the roster, and schedules the console switch
  (the R-52 session marker is W3's start-event sink, not creation).
- :func:`rivercrossing.ui.app._switch_console_to_ride` loads the ride
  from the store, renders its identity header (C1: name, logo, date,
  start, entry mode) and DRAFT state onto the view, and wires the
  store's append as the engine's event sink.

The wx boundary is the one mocked thing: ``require_wx`` is replaced
with a recorder so the deferred ``wx.CallAfter`` switch is observed
without constructing any GUI (T-10: wx is the GUI I/O boundary).
"""

import base64
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from conftest import gorba_config
from rivercrossing.ride import Event, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.store import Store
from rivercrossing.ui import app as app_module
from rivercrossing.ui.presenters.console import ConsolePresenter

if TYPE_CHECKING:
    import pytest

# A canonical 1x1 transparent PNG (67 bytes) -- the ride-logo BLOB the
# store writes and this module re-materializes, not a placeholder.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
    "AAAABJRU5ErkJggg=="
)


class _FakeConsoleView:
    """Record every render call a console switch makes."""

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
        """Record the teams-chip visibility verdict (R-11, W12)."""
        self.calls.append(("set_team_ui_visible", visible))

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


def _roster() -> Roster:
    """Build the one-entry roster the switch must rebuild."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="12")
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


def test_persist_created_ride_persists_roster_and_schedules_console_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """New Ride submit persists the ride and roster; no session marker.

    W3 moved the R-52 resume marker from ride creation to the audit
    log: ``set_active_ride`` now runs when the ride's ``start`` event
    is appended (the ``_wire_store_append`` sink), so a never-started
    ride never offers "continue" at the next launch. Creation itself
    leaves ``app_session.active_ride_id`` NULL and still schedules the
    console switch.
    """
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        view = _FakeConsoleView()
        context = _context(store=store, view=view, roster=_roster())
        fake_wx = _FakeWx()
        monkeypatch.setattr(app_module, "require_wx", lambda: fake_wx)

        app_module._persist_created_ride(context, gorba_config())

        rides = store.rides()
        assert [ride.name for ride in rides] == ["GORBA EPIC 2026"]
        ride_id = rides[0].id
        assert [entry.plate for entry in store.roster_for(ride_id).entries] == ["12"]
        session = store._conn.execute(
            "SELECT active_ride_id FROM app_session ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert session["active_ride_id"] is None
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
        ride_id = store.create_ride(gorba_config())
        store.save_roster(ride_id, _roster())
        view = _FakeConsoleView()
        context = _context(store=store, view=view, roster=_roster())

        app_module._switch_console_to_ride(context, ride_id)

        assert context.active_ride_id == ride_id
        assert [entry.plate for entry in context.roster.entries] == ["12"]
        assert [name for name, _arg in view.calls] == [
            "set_team_ui_visible",
            "set_presenter",
            "show_ride_header",
            "set_state",
            "show_feed",
            "show_counters",
            "focus_entry",
        ]
        swapped = next(arg for name, arg in view.calls if name == "set_presenter")
        assert isinstance(swapped, ConsolePresenter)
        assert swapped.engine.state is RideStatus.DRAFT
        assert (
            "show_ride_header",
            {
                "name": "GORBA EPIC 2026",
                "logo": None,
                "event_date": date(2026, 9, 20),
                "planned_start": datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive, by design
                "entry_mode": EntryMode.MIXED,
                "venue": "Sea to Sky Gondola",
                "organizer": "GORBA",
                "scorer": "K. Singh",
                "lap_km": 8.0,
            },
        ) in view.calls
        assert ("set_state", (RideStatus.DRAFT, False)) in view.calls
        # W12/R-11: the presenter pushes the teams-chip verdict on
        # birth -- this mixed roster keeps the Teams chip visible.
        assert ("set_team_ui_visible", True) in view.calls
        # The engine's event sink is wired to the store for this ride.
        swapped.engine.on_event(
            Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        )
        assert [row.action for row in store.audit_rows(ride_id)] == ["start"]
    finally:
        store.close()


def test_switch_console_to_ride_rematerializes_the_stored_ride_logo(
    tmp_path: Path,
) -> None:
    """C1: a store ride's logo BLOB reaches the header as a file path.

    ``Store.load_engine`` used to drop the ride-level ``ride.logo_png``
    BLOB (``logo_path=None``), so a reloaded ride could never render
    its own logo. The header now receives that file's path.
    """
    db_path = tmp_path / "rides.db"
    logo = tmp_path / "logo.png"
    logo.write_bytes(_TINY_PNG)
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(replace(gorba_config(), logo_path=logo))
        store.save_roster(ride_id, _roster())
        view = _FakeConsoleView()
        context = _context(store=store, view=view, roster=_roster())

        app_module._switch_console_to_ride(context, ride_id)

        header = next(arg for name, arg in view.calls if name == "show_ride_header")
        rendered_logo = header["logo"]
        assert isinstance(rendered_logo, Path)
        assert rendered_logo.read_bytes() == _TINY_PNG
    finally:
        store.close()
