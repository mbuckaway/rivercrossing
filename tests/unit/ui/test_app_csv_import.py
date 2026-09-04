# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for app.py's Import Riders CSV store wiring (R-74).

The E3.4 import route commits a picked CSV into the in-memory
``_RouteContext.roster`` via ``rider_editor.run_csv_import_flow``;
R-74's full scripted race imports a riders CSV into a store-backed
ride and expects that roster to survive the crash/quit relaunches,
which requires the route to persist the committed roster to the
active store ride (``Store.save_roster``). That wiring is the gap this
module pins -- headless, with a real Store over a temp file and a
stubbed ``run_csv_import_flow``, before any wx window is built (the
same headless route-test shape ``test_app_exports.py`` /
``test_app_ride_switch.py`` use).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING
from unittest.mock import Mock

from rivercrossing.ride import RideConfig
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.store import Store
from rivercrossing.ui import app as app_module
from rivercrossing.ui.views import rider_editor

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _StubFrame:
    """A minimal frame: status notices are captured, nothing else."""

    def __init__(self) -> None:
        """Start with no notices."""
        self.notices: list[str] = []

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name
        """Record *text* as the latest notice."""
        self.notices.append(text)


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


def _staged_roster() -> Roster:
    """Build the one-entry roster saved before the CSV import."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="12")
    return roster


def _imported_roster() -> Roster:
    """Build the roster a committed CSV import leaves in memory."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Rider", last_name="01", plate="1")
    roster.create_solo_entry(first_name="Rider", last_name="02", plate="2")
    return roster


def _context(
    *,
    store: Store | None,
    ride_id: int | None,
    roster: Roster | None = None,
) -> app_module._RouteContext:
    """Build a route context carrying *store*, *ride_id*, *roster*."""
    return app_module._RouteContext(
        frame=_StubFrame(),
        resource=None,
        roster=roster if roster is not None else _imported_roster(),
        app=None,
        theme_controller=None,
        store=store,
        active_ride_id=ride_id,
    )


def test_handle_import_csv_committed_import_persists_roster_to_the_active_ride(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A committed CSV import saves the roster to the active ride."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
        store.save_roster(ride_id, _staged_roster())
        context = _context(store=store, ride_id=ride_id)
        monkeypatch.setattr(rider_editor, "run_csv_import_flow", lambda _f, _r: True)

        app_module._handle_import_csv(context)

        assert [entry.plate for entry in store.roster_for(ride_id).entries] == ["1", "2"]
    finally:
        store.close()


def test_handle_import_csv_cancelled_import_leaves_the_stored_roster_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled import (flow returns False) persists nothing."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
        store.save_roster(ride_id, _staged_roster())
        context = _context(store=store, ride_id=ride_id)
        monkeypatch.setattr(rider_editor, "run_csv_import_flow", lambda _f, _r: False)

        app_module._handle_import_csv(context)

        assert [entry.plate for entry in store.roster_for(ride_id).entries] == ["12"]
    finally:
        store.close()


def test_handle_import_csv_without_a_store_backed_ride_still_runs_the_import_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrap (no store ride) keeps the in-memory-only import."""
    roster = _imported_roster()
    context = _context(store=None, ride_id=None, roster=roster)
    flow = Mock(return_value=True)
    monkeypatch.setattr(rider_editor, "run_csv_import_flow", flow)

    app_module._handle_import_csv(context)

    flow.assert_called_once_with(context.frame, context.roster)
    assert context.frame.notices == []
    assert context.roster is roster
