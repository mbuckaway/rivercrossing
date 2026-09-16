# SPDX-License-Identifier: GPL-3.0-only
"""Headless pin for the Simulation route's dialog seams (G9).

``app._decorate_simulation`` binds ``SimulatorDialog`` over the live
engine and roster the route context threads in, seeding the dialog
from the persisted settings so it opens on the operator's last-used
counts. This module drives that seam with the ``SimulatorDialog``
class swapped for a recorder -- the pattern
``test_app_open_target.py`` established for the Standings route -- and
pins the exact arguments the dialog is built with: the five spins, the
three G9 behaviour counts and the average rider speed the interval
default derives from. No wx window, app or dialog is ever constructed.

The same seam's no-ride half is pinned here too. A route-level context
with no console gets the dialog in its trimmed form -- ``engine=None``,
``roster=None`` and ``new_ride_btn``'s own callback
(:func:`rivercrossing.ui.app._create_simulator_test_ride`) -- and that
callback builds the next free "GORBA Test Ride #N" and hands it to the
New Ride persist. The store-less bootstrap the trimmed dialog is
reachable from has no database to create a ride in, so the flow says
so on the status bar instead; the dialog's own close-persist is
pinned here too, since a view with no presenter must write nothing.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import TYPE_CHECKING

import pytest

from conftest import gorba_config
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.store import Store
from rivercrossing.ui import app as app_module
from rivercrossing.ui.presenters.ride_defaults import DEFAULT_ORGANIZER, DEFAULT_VENUE
from rivercrossing.ui.presenters.settings import default_settings

if TYPE_CHECKING:
    from pathlib import Path

    from rivercrossing.ride import RideConfig

# The calendar day the trimmed dialog's New Ride is pinned to: the SUT's
# own ``date.today()`` is swapped for this stub, so the config it builds
# is deterministic rather than midnight-flaky.
_EVENT_DAY = date(2026, 9, 20)

# The gun time the flow combines with that day.
_PLANNED_START = datetime(2026, 9, 20, 10, 0)  # noqa: DTZ001 -- naive local, RideConfig's own contract


class _PresenterStub:
    """A console-presenter stub: the live engine, nothing else."""

    def __init__(self, engine: object) -> None:
        """Store the engine the dialog is built over."""
        self.engine = engine


class _DialogRecorder:
    """A ``SimulatorDialog`` double recording one construction."""

    def __init__(self, dialog: object, **kwargs: object) -> None:
        """Record the decorated window and every keyword seed."""
        self.dialog = dialog
        self.kwargs = kwargs


class _NoRideViewStub:
    """A no-ride ``SimulatorDialog`` stand-in: no presenter threaded."""

    def __init__(self) -> None:
        """Stand in the trimmed dialog's own attributes."""
        self.presenter = None
        self.sim_values = (175, 40, 15, 1, 45)
        self.sim_behaviors = (1, 0, 0)


class _NoticeFrame:
    """A frame stand-in recording the status-bar notices."""

    def __init__(self, notices: list[str]) -> None:
        """Share the caller's notice log."""
        self.notices = notices

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record one status-bar notice."""
        self.notices.append(text)


class _FrozenDate:
    """A ``date`` stand-in pinned to :data:`_EVENT_DAY`."""

    @staticmethod
    def today() -> date:
        """Answer the frozen calendar day."""
        return _EVENT_DAY


def _patch_simulator_dialog(monkeypatch: pytest.MonkeyPatch) -> list[_DialogRecorder]:
    """Swap ``SimulatorDialog`` for a recorder, returning its log."""
    from rivercrossing.ui.views import simulator  # noqa: PLC0415 -- the seam under test

    built: list[_DialogRecorder] = []

    def _record(dialog: object, **kwargs: object) -> _DialogRecorder:
        recorder = _DialogRecorder(dialog, **kwargs)
        built.append(recorder)
        return recorder

    monkeypatch.setattr(simulator, "SimulatorDialog", _record)
    return built


def _context(
    *,
    presenter: object | None,
    frame: object | None = None,
    store: Store | None = None,
) -> app_module._RouteContext:
    """Build a route context over *frame*, *store* and the settings."""
    context = app_module._RouteContext(
        frame=object() if frame is None else frame,
        resource=object(),
        roster=Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED),
        app=object(),
        theme_controller=object(),
        store=store,
    )
    context.settings = replace(
        default_settings(),
        sim_riders=37,
        sim_teams=6,
        sim_solo=5,
        sim_laps=4,
        sim_interval=9,
        sim_short_laps=2,
        sim_lapped=1,
        sim_team_stop=3,
        avg_speed_kmh=21.5,
    )
    context.presenter = presenter  # type: ignore[assignment] -- the stub answers the seam's engine read
    return context


def _store_with_rides(tmp_path: Path, names: tuple[str, ...]) -> Store:
    """Open a temp store holding one ride per *names* entry."""
    store = Store.open(tmp_path / "rides.db")
    for name in names:
        store.create_ride(replace(gorba_config(), name=name))
    return store


def test_decorate_simulation_given_live_settings_seeds_the_dialog_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G9: the dialog opens on all eight persisted counts."""
    built = _patch_simulator_dialog(monkeypatch)
    engine = object()
    context = _context(presenter=_PresenterStub(engine))
    window = object()

    view = app_module._decorate_simulation(context, window)

    assert [record.kwargs for record in built] == [
        {
            "engine": engine,
            "roster": context.roster,
            "sim_riders": 37,
            "sim_teams": 6,
            "sim_solo": 5,
            "sim_laps": 4,
            "sim_interval": 9,
            "sim_short_laps": 2,
            "sim_lapped": 1,
            "sim_team_stop": 3,
            "avg_speed_kmh": 21.5,
        }
    ]
    assert (built[0].dialog, view) == (window, built[0])


def test_decorate_simulation_without_a_presenter_builds_the_trimmed_no_ride_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A console-less context opens the New-Ride-only dialog."""
    built = _patch_simulator_dialog(monkeypatch)
    context = _context(presenter=None)
    window = object()

    view = app_module._decorate_simulation(context, window)

    assert (built[0].dialog, view) == (window, built[0])
    assert set(built[0].kwargs) == {"engine", "roster", "on_new_ride"}
    assert (built[0].kwargs["engine"], built[0].kwargs["roster"]) == (None, None)
    assert callable(built[0].kwargs["on_new_ride"])


def test_decorate_simulation_no_ride_new_ride_posts_the_no_store_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The trimmed dialog's one live action runs the New Ride flow."""
    built = _patch_simulator_dialog(monkeypatch)
    notices: list[str] = []
    context = _context(presenter=None, frame=_NoticeFrame(notices))
    app_module._decorate_simulation(context, object())
    new_ride = built[0].kwargs["on_new_ride"]

    new_ride()

    assert notices == ["New Ride — no store is open"]


def test_persist_simulator_changes_given_no_presenter_persists_nothing(
    tmp_path: Path,
) -> None:
    """The no-ride dialog's close persists neither roster nor spins.

    The trimmed dialog's ``presenter`` is ``None`` (its own no-ride
    open), so the close-persist must return before it reads
    ``roster_changed``: the view's authored spin defaults are not a
    session's choices, and writing them back would overwrite the
    operator's saved counts.
    """
    context = _context(presenter=None)
    context.settings_path = tmp_path / "settings.json"

    app_module._persist_simulator_changes(context, _NoRideViewStub())

    assert context.settings.sim_riders == 37
    assert not context.settings_path.exists()


def test_create_simulator_test_ride_given_no_store_posts_the_no_store_notice() -> None:
    """A store-less bootstrap session creates no ride and says so."""
    notices: list[str] = []
    context = _context(presenter=None, frame=_NoticeFrame(notices))

    app_module._create_simulator_test_ride(context)

    assert notices == ["New Ride — no store is open"]


@pytest.mark.parametrize(
    ("names", "expected_name"),
    [
        ((), "GORBA Test Ride #1"),
        (("GORBA Test Ride #3",), "GORBA Test Ride #4"),
        (
            ("GORBA Test Ride #1", "GORBA Test Ride #2", "GORBA Test Ride #5"),
            "GORBA Test Ride #6",
        ),
    ],
    ids=("no-rides", "one-ride", "many-rides"),
)
def test_create_simulator_test_ride_given_a_store_persists_the_next_test_ride(  # noqa: PLR0913, PLR0917 -- the parametrize row's two inputs + (tmp_path, monkeypatch)
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    names: tuple[str, ...],
    expected_name: str,
) -> None:
    """The dialog's New Ride hands the persist the next test ride."""
    store = _store_with_rides(tmp_path, names)
    try:
        context = _context(presenter=None, store=store)
        created: list[RideConfig] = []
        monkeypatch.setattr(
            app_module, "_persist_created_ride", lambda _ctx, config: created.append(config)
        )
        monkeypatch.setattr(app_module, "date", _FrozenDate)

        app_module._create_simulator_test_ride(context)

        assert [config.name for config in created] == [expected_name]
        assert (created[0].event_date, created[0].planned_start) == (_EVENT_DAY, _PLANNED_START)
        assert (created[0].venue, created[0].organizer) == (DEFAULT_VENUE, DEFAULT_ORGANIZER)
    finally:
        store.close()
