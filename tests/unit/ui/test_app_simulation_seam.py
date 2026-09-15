# SPDX-License-Identifier: GPL-3.0-only
"""Headless pin for the Simulation route's dialog seeds (G9).

``app._decorate_simulation`` binds ``SimulatorDialog`` over the live
engine and roster the route context threads in, seeding the dialog
from the persisted settings so it opens on the operator's last-used
counts. This module drives that seam with the ``SimulatorDialog``
class swapped for a recorder -- the pattern
``test_app_open_target.py`` established for the Standings route -- and
pins the exact arguments the dialog is built with: the five spins, the
three G9 behaviour counts and the average rider speed the interval
default derives from. No wx window, app or dialog is ever constructed.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui.presenters.settings import default_settings

if TYPE_CHECKING:
    import pytest


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


def _context(*, presenter: object | None) -> app_module._RouteContext:
    """Build a route context on non-default settings and *presenter*."""
    context = app_module._RouteContext(
        frame=object(),
        resource=object(),
        roster=Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED),
        app=object(),
        theme_controller=object(),
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


def test_decorate_simulation_without_a_presenter_builds_no_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A route-level context with no console builds nothing."""
    built = _patch_simulator_dialog(monkeypatch)

    view = app_module._decorate_simulation(_context(presenter=None), object())

    assert (view, built) == (None, [])
