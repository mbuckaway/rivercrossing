# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the Rider Simulator dialog's control state.

Only what genuinely needs no window is pinned here, in the
``test_results_win.py`` / ``test_rider_editor_dialog.py`` style:
:meth:`SimulatorDialog._apply_roster_state` (and the spin seeding /
snapshot behaviour) driven against a shell that owns only the generator
controls and the presenter's roster. The live layout -- real spin
controls on a real dialog -- stays with the (functional) suite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import wx

from rivercrossing.roster import EntryMode, EntryType, PlateModel, Roster
from rivercrossing.ui.presenters.simulator import SimulatorPresenter
from rivercrossing.ui.views import simulator as simulator_module
from rivercrossing.ui.views.simulator import SimulatorDialog

if TYPE_CHECKING:
    import pytest


class _Control:
    """A wx control double recording its Show and Enable calls.

    Starts shown, the state XRC gives every control, so a test can tell
    "left alone" from "hidden".
    """

    def __init__(self) -> None:
        """Start shown and enabled."""
        self.shown = True
        self.enabled: bool | None = None

    def Show(self, visible: bool) -> None:  # noqa: N802, FBT001 -- wx API name and bool
        """Record the show/hide."""
        self.shown = visible

    def Enable(self, enabled: bool) -> None:  # noqa: N802, FBT001 -- wx API name and bool
        """Record the enablement."""
        self.enabled = enabled

    def GetContainingSizer(self) -> None:  # noqa: N802 -- wx API name
        """Return None: this shell carries no sizer."""


class _Spin(_Control):
    """A wxSpinCtrl double carrying one integer value."""

    def __init__(self, value: int) -> None:
        """Start shown, enabled and holding *value*."""
        super().__init__()
        self.value = value

    def GetValue(self) -> int:  # noqa: N802 -- wx API name
        """Return the current spin value."""
        return self.value

    def SetValue(self, value: int) -> None:  # noqa: N802 -- wx API name
        """Set the spin value."""
        self.value = value


class _Roster:
    """A roster double owning only what the view reads."""

    def __init__(self, *, entry_mode: EntryMode, loaded: bool) -> None:
        """Store the entry mode and whether any entry exists."""
        self.entry_mode = entry_mode
        self.entries = (object(),) if loaded else ()


class _Presenter:
    """A presenter double exposing its roster."""

    def __init__(self, roster: _Roster) -> None:
        """Store the roster the view reads."""
        self.roster = roster


class _Shell:
    """A SimulatorDialog shell owning only the generator controls."""

    def __init__(self, *, entry_mode: EntryMode, loaded: bool) -> None:
        """Build the roster double and one double per control."""
        self.presenter = _Presenter(_Roster(entry_mode=entry_mode, loaded=loaded))
        self.riders_spin = _Control()
        self.teams_spin = _Control()
        self.solo_spin = _Control()
        self.gen_riders_btn = _Control()


def test_apply_roster_state_given_loaded_roster_disables_every_generator_control() -> None:
    """A loaded roster disables every generator control."""
    shell = _Shell(entry_mode=EntryMode.MIXED, loaded=True)

    SimulatorDialog._apply_roster_state(shell)

    assert [
        shell.riders_spin.enabled,
        shell.teams_spin.enabled,
        shell.solo_spin.enabled,
        shell.gen_riders_btn.enabled,
    ] == [False, False, False, False]


def test_apply_roster_state_given_empty_roster_enables_every_generator_control() -> None:
    """An empty roster leaves every generator control live."""
    shell = _Shell(entry_mode=EntryMode.MIXED, loaded=False)

    SimulatorDialog._apply_roster_state(shell)

    assert [
        shell.riders_spin.enabled,
        shell.teams_spin.enabled,
        shell.solo_spin.enabled,
        shell.gen_riders_btn.enabled,
    ] == [True, True, True, True]


def test_apply_roster_state_given_solo_mode_hides_the_team_and_solo_fields() -> None:
    """A SOLO-only ride hides the team fields."""
    shell = _Shell(entry_mode=EntryMode.SOLO, loaded=False)

    SimulatorDialog._apply_roster_state(shell)

    assert (shell.teams_spin.shown, shell.solo_spin.shown) == (False, False)
    assert (shell.riders_spin.shown, shell.gen_riders_btn.shown) == (True, True)


def test_apply_roster_state_given_solo_mode_and_loaded_roster_hides_and_disables() -> None:
    """T-13: SOLO mode and a loaded roster combine."""
    shell = _Shell(entry_mode=EntryMode.SOLO, loaded=True)

    SimulatorDialog._apply_roster_state(shell)

    assert (shell.teams_spin.shown, shell.teams_spin.enabled) == (False, False)
    assert shell.riders_spin.enabled is False


def test_apply_roster_state_given_mixed_mode_keeps_the_team_and_solo_fields_shown() -> None:
    """A MIXED ride keeps every generator control visible (T-3)."""
    shell = _Shell(entry_mode=EntryMode.MIXED, loaded=False)

    SimulatorDialog._apply_roster_state(shell)

    assert (shell.teams_spin.shown, shell.solo_spin.shown) == (True, True)
    assert shell.gen_riders_btn.shown is True


# --- plan §1: seeded spins, sim_values and the single Generate button


class _FakeSizer:
    """A sizer double recording the InfoBar insert."""

    def __init__(self) -> None:
        """Start with nothing inserted."""
        self.inserted: list[object] = []

    def Insert(self, _index: int, window: object, _flag: int, _border: int) -> None:  # noqa: N802
        """Record one inserted window."""
        self.inserted.append(window)


class _FakeDialog:
    """A wx.Dialog double: bindable, sizer-owning and closable."""

    def __init__(self) -> None:
        """Start with an empty bind log and its own sizer."""
        self.bound: list[object] = []
        self.sizer = _FakeSizer()
        self.min_size: object | None = None
        self.ended: list[int] = []
        self.layouts = 0

    def Bind(self, _event: object, _handler: object, control: object) -> None:  # noqa: N802
        """Record one bound control."""
        self.bound.append(control)

    def GetSizer(self) -> _FakeSizer:  # noqa: N802 -- wx API name
        """Return the dialog's sizer double."""
        return self.sizer

    def SetMinSize(self, size: object) -> None:  # noqa: N802 -- wx API name
        """Record the applied minimum size."""
        self.min_size = size

    def Fit(self) -> None:  # noqa: N802 -- wx API name
        """No-op: there is no real layout to fit."""

    def EndModal(self, result: int) -> None:  # noqa: N802 -- wx API name
        """Record one modal close."""
        self.ended.append(result)

    def Layout(self) -> None:  # noqa: N802 -- wx API name
        """Record one layout pass."""
        self.layouts += 1


class _FakeInfobar:
    """An InfoBar double recording the messages shown on it."""

    def __init__(self) -> None:
        """Start with no messages."""
        self.messages: list[str] = []

    def ShowMessage(self, message: str, _flags: object) -> None:  # noqa: N802 -- wx API name
        """Record one shown message."""
        self.messages.append(message)


class _RecordingRunning:
    """A SimRunningDialog stand-in recording its race settings."""

    def __init__(  # noqa: PLR0913 -- mirrors the SUT's own keyword constructor
        self,
        _dialog: object,
        *,
        engine: object,
        roster: object,
        laps: int,
        interval_minutes: int,
    ) -> None:
        """Record the race settings handed to the running dialog."""
        self.engine = engine
        self.roster = roster
        self.laps = laps
        self.interval_minutes = interval_minutes
        self.ran = False

    def run(self) -> None:
        """Record that the race was started."""
        self.ran = True


def _generator_shell(  # noqa: PLR0913 -- (roster) + the five spin overrides
    roster: Roster,
    *,
    riders: int = 10,
    teams: int = 2,
    solo: int = 2,
    laps: int = 1,
    interval: int = 1,
) -> SimulatorDialog:
    """Build a dialog shell over a real presenter and spin doubles."""
    shell = object.__new__(SimulatorDialog)
    shell.presenter = SimulatorPresenter(None, roster)
    shell.riders_spin = _Spin(riders)
    shell.teams_spin = _Spin(teams)
    shell.solo_spin = _Spin(solo)
    shell.laps_spin = _Spin(laps)
    shell.interval_spin = _Spin(interval)
    shell.gen_riders_btn = _Control()
    shell.go_btn = _Control()
    shell.sim_infobar = _FakeInfobar()
    shell.dialog = _FakeDialog()
    shell.sim_values = (riders, teams, solo, laps, interval)
    return shell


def _patch_find(monkeypatch: pytest.MonkeyPatch, spins: dict[str, _Control]) -> None:
    """Resolve every control name to its double, never a real window."""

    def _find(_self: SimulatorDialog, name: str, _expected: object = None) -> _Control:
        return spins[name]

    monkeypatch.setattr(SimulatorDialog, "_find", _find)
    monkeypatch.setattr(SimulatorDialog, "_build_infobar", lambda _self: _FakeInfobar())


def test_simulator_dialog_seeds_the_spins_from_the_passed_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan §1: opening restores the persisted spin defaults."""
    spins = {
        "riders_spin": _Spin(10),
        "teams_spin": _Spin(2),
        "solo_spin": _Spin(2),
        "laps_spin": _Spin(1),
        "interval_spin": _Spin(1),
        "gen_riders_btn": _Control(),
        "go_btn": _Control(),
    }
    _patch_find(monkeypatch, spins)

    view = SimulatorDialog(
        _FakeDialog(),
        engine=None,
        roster=Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED),
        sim_riders=37,
        sim_teams=6,
        sim_solo=5,
        sim_laps=4,
        sim_interval=9,
    )

    assert (view.riders_spin.GetValue(), view.teams_spin.GetValue()) == (37, 6)
    assert (view.solo_spin.GetValue(), view.laps_spin.GetValue()) == (5, 4)
    assert (view.interval_spin.GetValue(), view.sim_values) == (9, (37, 6, 5, 4, 9))


def test_simulator_dialog_seeds_the_xrc_defaults_when_none_are_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The XRC values remain the fallback when no seeds are passed."""
    spins = {
        "riders_spin": _Spin(10),
        "teams_spin": _Spin(2),
        "solo_spin": _Spin(2),
        "laps_spin": _Spin(1),
        "interval_spin": _Spin(1),
        "gen_riders_btn": _Control(),
        "go_btn": _Control(),
    }
    _patch_find(monkeypatch, spins)

    view = SimulatorDialog(
        _FakeDialog(),
        engine=None,
        roster=Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED),
    )

    assert view.sim_values == (10, 2, 2, 1, 1)


def test_snapshot_sim_values_records_the_current_spins() -> None:
    """sim_values always reflects the five spins at snapshot time."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    shell = _generator_shell(roster, riders=3, teams=4, solo=5, laps=6, interval=7)

    SimulatorDialog._snapshot_sim_values(shell)

    assert shell.sim_values == (3, 4, 5, 6, 7)


def test_on_generate_riders_given_a_mixed_roster_creates_teams_then_riders() -> None:
    """Plan §1: the single Generate button runs generate_riders."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    shell = _generator_shell(roster, riders=10, teams=2, solo=2)

    SimulatorDialog._on_generate_riders(shell, None)

    teams = [entry for entry in roster.entries if entry.type is EntryType.TEAM]
    solos = [entry for entry in roster.entries if entry.type is EntryType.SOLO]
    assert ([entry.team_size for entry in teams], len(solos)) == ([4, 4], 2)
    assert shell.dialog.ended == [wx.ID_OK]


def test_on_generate_riders_given_a_solo_roster_creates_solo_riders_only() -> None:
    """A SOLO-only ride generates solo entries through the button."""
    roster = Roster(entry_mode=EntryMode.SOLO, plate_model=PlateModel.RIDER_POOLED)
    shell = _generator_shell(roster, riders=3)

    SimulatorDialog._on_generate_riders(shell, None)

    assert [entry.type for entry in roster.entries] == [EntryType.SOLO] * 3
    assert shell.dialog.ended == [wx.ID_OK]


def test_on_generate_riders_given_a_refused_count_warns_and_keeps_the_dialog_open() -> None:
    """A refused count lands on the InfoBar; the dialog stays open."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    shell = _generator_shell(roster, riders=2, teams=1, solo=5)

    SimulatorDialog._on_generate_riders(shell, None)

    assert shell.sim_infobar.messages == ["solo must be between 0 and 2"]
    assert (shell.dialog.ended, roster.entries) == ([], ())


def test_on_go_given_valid_settings_runs_the_race_and_snapshots_the_spins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan §1: GO snapshots the spins before closing the dialog."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    shell = _generator_shell(roster, laps=3, interval=7)
    shell.sim_values = (9, 9, 9, 9, 9)
    created: list[_RecordingRunning] = []

    def _build(dialog: object, **kwargs: object) -> _RecordingRunning:
        running = _RecordingRunning(dialog, **kwargs)
        created.append(running)
        return running

    monkeypatch.setattr(simulator_module, "_load_running_window", lambda _parent: _Control())
    monkeypatch.setattr(simulator_module, "SimRunningDialog", _build)

    SimulatorDialog._on_go(shell, None)

    assert (shell.sim_values, created[0].laps, created[0].interval_minutes) == (
        (10, 2, 2, 3, 7),
        3,
        7,
    )
    assert (created[0].ran, shell.dialog.ended) == (True, [wx.ID_OK])


def test_on_go_given_an_out_of_range_lap_count_warns_and_keeps_the_dialog_open() -> None:
    """A refused race setting lands on the InfoBar and stays open."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    shell = _generator_shell(roster, laps=0, interval=1)

    SimulatorDialog._on_go(shell, None)

    assert shell.sim_infobar.messages == ["laps must be at least 1"]
    assert (shell.dialog.ended, shell.dialog.layouts) == ([], 1)
