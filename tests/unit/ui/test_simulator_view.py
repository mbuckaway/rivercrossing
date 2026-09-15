# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the Rider Simulator dialog's control state.

Only what genuinely needs no window is pinned here, in the
``test_results_win.py`` / ``test_rider_editor_dialog.py`` style:
:meth:`SimulatorDialog._apply_roster_state` (and the spin seeding /
snapshot behaviour) driven against a shell that owns only the generator
controls and the presenter's roster. The live layout -- real spin
controls on a real dialog -- stays with the (functional) suite.

Phase 2 adds the Check button (a system modal, never ``sim_infobar``),
the solo auto-fill on every riders/teams change, and the
speed-derived interval seed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import wx

from rivercrossing.roster import EntryMode, EntryType, PlateModel, Roster
from rivercrossing.ui.presenters.simulator import (
    SimOutcome,
    SimulatorPresenter,
    check_message,
)
from rivercrossing.ui.views import simulator as simulator_module
from rivercrossing.ui.views.simulator import SimRunningDialog, SimulatorDialog


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


class _Choice(_Control):
    """A wxChoice double carrying one selection index (G9)."""

    def __init__(self, selection: int) -> None:
        """Start shown, enabled and on item *selection*."""
        super().__init__()
        self.selection = selection

    def GetSelection(self) -> int:  # noqa: N802 -- wx API name
        """Return the selected item index."""
        return self.selection

    def SetSelection(self, selection: int) -> None:  # noqa: N802 -- wx API name
        """Select item *selection*."""
        self.selection = selection


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
        self.check_btn = _Control()
        self.gen_riders_btn = _Control()


def test_apply_roster_state_given_loaded_roster_disables_every_generator_control() -> None:
    """A loaded roster disables every generator control."""
    shell = _Shell(entry_mode=EntryMode.MIXED, loaded=True)

    SimulatorDialog._apply_roster_state(shell)

    assert [
        shell.riders_spin.enabled,
        shell.teams_spin.enabled,
        shell.solo_spin.enabled,
        shell.check_btn.enabled,
        shell.gen_riders_btn.enabled,
    ] == [False, False, False, False, False]


def test_apply_roster_state_given_empty_roster_enables_every_generator_control() -> None:
    """An empty roster leaves every generator control live."""
    shell = _Shell(entry_mode=EntryMode.MIXED, loaded=False)

    SimulatorDialog._apply_roster_state(shell)

    assert [
        shell.riders_spin.enabled,
        shell.teams_spin.enabled,
        shell.solo_spin.enabled,
        shell.check_btn.enabled,
        shell.gen_riders_btn.enabled,
    ] == [True, True, True, True, True]


def test_apply_roster_state_given_solo_mode_hides_the_team_and_solo_fields() -> None:
    """A SOLO-only ride hides the team fields and Check."""
    shell = _Shell(entry_mode=EntryMode.SOLO, loaded=False)

    SimulatorDialog._apply_roster_state(shell)

    assert (shell.teams_spin.shown, shell.solo_spin.shown) == (False, False)
    assert (shell.check_btn.shown, shell.riders_spin.shown) == (False, True)
    assert shell.gen_riders_btn.shown is True


def test_apply_roster_state_given_solo_mode_and_loaded_roster_hides_and_disables() -> None:
    """T-13: SOLO mode and a loaded roster combine."""
    shell = _Shell(entry_mode=EntryMode.SOLO, loaded=True)

    SimulatorDialog._apply_roster_state(shell)

    assert (shell.teams_spin.shown, shell.teams_spin.enabled) == (False, False)
    assert (shell.check_btn.shown, shell.check_btn.enabled) == (False, False)
    assert shell.riders_spin.enabled is False


def test_apply_roster_state_given_mixed_mode_keeps_the_team_and_solo_fields_shown() -> None:
    """A MIXED ride keeps every generator control visible (T-3)."""
    shell = _Shell(entry_mode=EntryMode.MIXED, loaded=False)

    SimulatorDialog._apply_roster_state(shell)

    assert (shell.teams_spin.shown, shell.solo_spin.shown) == (True, True)
    assert (shell.check_btn.shown, shell.gen_riders_btn.shown) == (True, True)


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
        short_laps: int = 0,
        lapped: int = 0,
        team_stop: int = 0,
    ) -> None:
        """Record the race settings handed to the running dialog."""
        self.engine = engine
        self.roster = roster
        self.laps = laps
        self.interval_minutes = interval_minutes
        self.short_laps = short_laps
        self.lapped = lapped
        self.team_stop = team_stop
        self.ran = False

    def run(self) -> None:
        """Record that the race was started."""
        self.ran = True


class _RefusingPresenter:
    """A presenter stand-in whose generator refuses the counts."""

    def __init__(self, roster: Roster, error: ValueError) -> None:
        """Store the roster the view reads and the refusal to raise."""
        self.roster = roster
        self.error = error

    def generate_riders(self, *_args: object, **_kwargs: object) -> None:
        """Raise the stored refusal, as the real generator would."""
        raise self.error


def _generator_shell(  # noqa: PLR0913 -- (roster) + the five spin and three choice overrides
    roster: Roster,
    *,
    riders: int = 175,
    teams: int = 40,
    solo: int = 15,
    laps: int = 1,
    interval: int = 45,
    short_laps: int = 1,
    lapped: int = 0,
    team_stop: int = 0,
    engine: object = None,
) -> SimulatorDialog:
    """Build a shell over a real presenter and the control doubles."""
    shell = object.__new__(SimulatorDialog)
    shell.presenter = SimulatorPresenter(engine, roster)
    shell.riders_spin = _Spin(riders)
    shell.teams_spin = _Spin(teams)
    shell.solo_spin = _Spin(solo)
    shell.laps_spin = _Spin(laps)
    shell.interval_spin = _Spin(interval)
    shell.short_lap_choice = _Choice(short_laps)
    shell.lapped_choice = _Choice(lapped)
    shell.team_stop_choice = _Choice(team_stop)
    shell.check_btn = _Control()
    shell.gen_riders_btn = _Control()
    shell.go_btn = _Control()
    shell.sim_infobar = _FakeInfobar()
    shell.dialog = _FakeDialog()
    shell.sim_values = (riders, teams, solo, laps, interval)
    shell.sim_behaviors = (short_laps, lapped, team_stop)
    return shell


def _engine(lap_km: float = 8.0) -> SimpleNamespace:
    """Return an engine double carrying the ride's own config."""
    return SimpleNamespace(config=SimpleNamespace(lap_km=lap_km))


def _controls() -> dict[str, _Control]:
    """Return a control double per name the dialog resolves."""
    return {
        "riders_spin": _Spin(175),
        "teams_spin": _Spin(40),
        "solo_spin": _Spin(15),
        "laps_spin": _Spin(1),
        "interval_spin": _Spin(45),
        "short_lap_choice": _Choice(1),
        "lapped_choice": _Choice(0),
        "team_stop_choice": _Choice(0),
        "check_btn": _Control(),
        "gen_riders_btn": _Control(),
        "go_btn": _Control(),
    }


def _patch_find(monkeypatch: pytest.MonkeyPatch, spins: dict[str, _Control]) -> None:
    """Resolve every control name to its double, never a real window."""

    def _find(_self: SimulatorDialog, name: str, _expected: object = None) -> _Control:
        return spins[name]

    monkeypatch.setattr(SimulatorDialog, "_find", _find)
    monkeypatch.setattr(SimulatorDialog, "_build_infobar", lambda _self: _FakeInfobar())


def _patch_modals(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[tuple[object, str, str]], list[tuple[object, str, str]]]:
    """Record the std_dialogs show_* calls instead."""
    info: list[tuple[object, str, str]] = []
    warning: list[tuple[object, str, str]] = []

    def _show_info(parent: object, title: str, message: str) -> None:
        info.append((parent, title, message))

    def _show_warning(parent: object, title: str, message: str) -> None:
        warning.append((parent, title, message))

    monkeypatch.setattr(simulator_module.std_dialogs, "show_info", _show_info)
    monkeypatch.setattr(simulator_module.std_dialogs, "show_warning", _show_warning)
    return info, warning


def test_simulator_dialog_seeds_the_spins_from_the_passed_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan §1: opening restores the spins and derives solo."""
    spins = _controls()
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
    assert (view.solo_spin.GetValue(), view.laps_spin.GetValue()) == (13, 4)
    assert (view.interval_spin.GetValue(), view.sim_values) == (9, (37, 6, 13, 4, 9))


def test_simulator_dialog_seeds_the_new_defaults_when_none_are_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The view's own defaults are the new 175/40/15 spins."""
    spins = _controls()
    _patch_find(monkeypatch, spins)

    view = SimulatorDialog(
        _FakeDialog(),
        engine=None,
        roster=Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED),
    )

    assert view.sim_values == (175, 40, 15, 1, 45)


@pytest.mark.parametrize(
    ("lap_km", "avg_speed_kmh", "expected"),
    [
        pytest.param(8.0, 12.0, 45, id="demo-ride"),
        pytest.param(5.0, 20.0, 20, id="shorter-lap-higher-speed"),
        pytest.param(30.0, 1.0, 240, id="clamped-at-the-spin-ceiling"),
    ],
)
def test_simulator_dialog_given_an_engine_seeds_the_interval_from_the_ride(  # noqa: PLR0913, PLR0917
    monkeypatch: pytest.MonkeyPatch, lap_km: float, avg_speed_kmh: float, expected: int
) -> None:
    """Plan §3: the interval opens on the ride's own speed default."""
    spins = _controls()
    _patch_find(monkeypatch, spins)

    view = SimulatorDialog(
        _FakeDialog(),
        engine=_engine(lap_km),
        roster=Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED),
        sim_interval=1,
        avg_speed_kmh=avg_speed_kmh,
    )

    assert view.interval_spin.GetValue() == expected


def test_simulator_dialog_given_a_solo_roster_leaves_solo_as_seeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SOLO-only ride leaves its hidden solo field as seeded."""
    spins = _controls()
    _patch_find(monkeypatch, spins)

    view = SimulatorDialog(
        _FakeDialog(),
        engine=None,
        roster=Roster(entry_mode=EntryMode.SOLO, plate_model=PlateModel.RIDER_POOLED),
        sim_solo=7,
    )

    assert view.solo_spin.GetValue() == 7


def test_simulator_dialog_binds_check_and_the_two_team_count_spins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan §1/§2: Check and both count spins are bound."""
    spins = _controls()
    _patch_find(monkeypatch, spins)

    view = SimulatorDialog(
        _FakeDialog(),
        engine=None,
        roster=Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED),
    )

    assert view.dialog.bound == [
        spins["gen_riders_btn"],
        spins["check_btn"],
        spins["go_btn"],
        spins["riders_spin"],
        spins["teams_spin"],
    ]


@pytest.mark.parametrize(
    ("short_laps", "lapped", "team_stop"),
    [
        pytest.param(0, 0, 0, id="every-choice-disabled"),
        pytest.param(1, 0, 0, id="the-product-defaults"),
        pytest.param(10, 10, 10, id="every-choice-at-its-ceiling"),
    ],
)
def test_simulator_dialog_seeds_the_three_behaviour_choices(  # noqa: PLR0913, PLR0917
    monkeypatch: pytest.MonkeyPatch, short_laps: int, lapped: int, team_stop: int
) -> None:
    """G9: the three dropdowns open on the persisted counts."""
    spins = _controls()
    _patch_find(monkeypatch, spins)

    view = SimulatorDialog(
        _FakeDialog(),
        engine=None,
        roster=Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED),
        sim_short_laps=short_laps,
        sim_lapped=lapped,
        sim_team_stop=team_stop,
    )

    assert (
        view.short_lap_choice.GetSelection(),
        view.lapped_choice.GetSelection(),
        view.team_stop_choice.GetSelection(),
    ) == (short_laps, lapped, team_stop)
    assert view.sim_behaviors == (short_laps, lapped, team_stop)


def test_simulator_dialog_seeds_the_behaviour_defaults_when_none_are_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The view's own defaults: one short-lap rider, nothing else."""
    spins = _controls()
    _patch_find(monkeypatch, spins)

    view = SimulatorDialog(
        _FakeDialog(),
        engine=None,
        roster=Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED),
    )

    assert view.sim_behaviors == (1, 0, 0)


def test_snapshot_sim_values_records_the_current_spins() -> None:
    """sim_values always reflects the five spins at snapshot time."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    shell = _generator_shell(roster, riders=3, teams=4, solo=5, laps=6, interval=7)

    SimulatorDialog._snapshot_sim_values(shell)

    assert shell.sim_values == (3, 4, 5, 6, 7)


def test_snapshot_sim_values_records_the_current_behaviour_choices() -> None:
    """G9: sim_behaviors reflects the three choices at snapshot time."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    shell = _generator_shell(roster, short_laps=2, lapped=1, team_stop=0)
    shell.short_lap_choice.SetSelection(3)
    shell.lapped_choice.SetSelection(4)
    shell.team_stop_choice.SetSelection(5)

    SimulatorDialog._snapshot_sim_values(shell)

    assert shell.sim_behaviors == (3, 4, 5)


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


def test_on_generate_riders_given_stale_solo_auto_corrects_and_generates() -> None:
    """Plan §1: Generate resolves solo, then proceeds."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    shell = _generator_shell(roster, riders=10, teams=2, solo=0)

    SimulatorDialog._on_generate_riders(shell, None)

    teams = [entry for entry in roster.entries if entry.type is EntryType.TEAM]
    assert (shell.solo_spin.GetValue(), [entry.team_size for entry in teams]) == (2, [4, 4])
    assert shell.dialog.ended == [wx.ID_OK]


def test_on_generate_riders_given_impossible_counts_shows_the_check_modal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan §2: below the team floor Generate explains."""
    info, warning = _patch_modals(monkeypatch)
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    shell = _generator_shell(roster, riders=1, teams=2, solo=0)

    SimulatorDialog._on_generate_riders(shell, None)

    assert warning == [(shell.dialog, "Rider Simulator", check_message(1, 2))]
    assert (info, shell.sim_infobar.messages) == ([], [])
    assert (shell.dialog.ended, roster.entries, shell.solo_spin.GetValue()) == ([], (), 0)


def test_on_generate_riders_given_a_refused_generation_warns_on_the_infobar() -> None:
    """A presenter refusal still lands on the InfoBar."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    shell = _generator_shell(roster, riders=10, teams=2)
    shell.presenter = _RefusingPresenter(
        roster, ValueError("team riders must be between 4 and 8, got 9")
    )

    SimulatorDialog._on_generate_riders(shell, None)

    assert shell.sim_infobar.messages == ["team riders must be between 4 and 8, got 9"]
    assert (shell.dialog.ended, shell.dialog.layouts) == ([], 1)


def test_on_check_given_a_valid_combination_shows_the_system_info_modal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan §2: Check explains it in a native OK-only modal."""
    info, warning = _patch_modals(monkeypatch)
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    shell = _generator_shell(roster, riders=175, teams=40)

    SimulatorDialog._on_check(shell, None)

    assert info == [(shell.dialog, "Rider Simulator", check_message(175, 40))]
    assert (warning, shell.sim_infobar.messages) == ([], [])


def test_on_check_given_impossible_counts_shows_the_system_warning_modal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An impossible field gets the warning icon."""
    info, warning = _patch_modals(monkeypatch)
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    shell = _generator_shell(roster, riders=10, teams=8)

    SimulatorDialog._on_check(shell, None)

    assert warning == [(shell.dialog, "Rider Simulator", check_message(10, 8))]
    assert (info, shell.sim_infobar.messages) == ([], [])


@pytest.mark.parametrize(
    ("riders", "teams", "solo", "expected"),
    [
        pytest.param(10, 2, 0, 2, id="fills-the-teams"),
        pytest.param(5, 2, 9, 0, id="teams-not-full"),
        pytest.param(1, 2, 5, 5, id="impossible-leaves-solo-alone"),
    ],
)
def test_on_team_counts_changed_given_riders_and_teams_fills_solo(  # noqa: PLR0913, PLR0917
    riders: int, teams: int, solo: int, expected: int
) -> None:
    """Plan §1: every riders/teams change recomputes solo."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    shell = _generator_shell(roster, riders=riders, teams=teams, solo=solo)

    SimulatorDialog._on_team_counts_changed(shell, None)

    assert shell.solo_spin.GetValue() == expected


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

    control = _Control()
    parents: list[object] = []

    def _load(parent: object) -> _Control:
        parents.append(parent)
        return control

    monkeypatch.setattr(simulator_module, "_load_running_window", _load)
    monkeypatch.setattr(simulator_module, "SimRunningDialog", _build)

    SimulatorDialog._on_go(shell, None)

    assert (shell.sim_values, created[0].laps, created[0].interval_minutes) == (
        (175, 40, 15, 3, 7),
        3,
        7,
    )
    assert (created[0].ran, shell.dialog.ended) == (True, [wx.ID_OK])
    # The running dialog stacks over the modal simulation dialog, so
    # the loader must be handed that window (never a fresh top-level).
    assert parents == [shell.dialog]


def test_on_go_given_behaviour_selections_threads_the_three_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G9: GO hands the three selected counts to the running dialog."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    shell = _generator_shell(roster, laps=3, interval=7, short_laps=2, lapped=1, team_stop=4)
    created: list[_RecordingRunning] = []

    def _build(dialog: object, **kwargs: object) -> _RecordingRunning:
        running = _RecordingRunning(dialog, **kwargs)
        created.append(running)
        return running

    monkeypatch.setattr(simulator_module, "_load_running_window", lambda _parent: _Control())
    monkeypatch.setattr(simulator_module, "SimRunningDialog", _build)

    SimulatorDialog._on_go(shell, None)

    assert (created[0].short_laps, created[0].lapped, created[0].team_stop) == (2, 1, 4)
    assert shell.sim_behaviors == (2, 1, 4)


class _RecordingPresenter:
    """A presenter double recording one run_simulation call."""

    def __init__(self) -> None:
        """Start with no recorded call and its own outcome."""
        self.calls: list[dict[str, object]] = []
        self.outcome = SimOutcome(cancelled=False, recorded=7, blocked=None)

    def run_simulation(self, laps: int, interval_minutes: int, **kwargs: object) -> SimOutcome:
        """Record the race settings and return the stored outcome."""
        self.calls.append({"laps": laps, "interval_minutes": interval_minutes, **kwargs})
        return self.outcome


def test_sim_running_dialog_run_threads_the_three_counts_into_the_presenter() -> None:
    """G9: the running dialog passes the counts through to the race."""
    shell = object.__new__(SimRunningDialog)
    shell.presenter = _RecordingPresenter()
    shell.laps = 3
    shell.interval_minutes = 7
    shell.short_laps = 2
    shell.lapped = 1
    shell.team_stop = 4
    shell.dialog = _FakeDialog()
    shell.outcome = None

    SimRunningDialog._run(shell)

    assert shell.presenter.calls == [
        {
            "laps": 3,
            "interval_minutes": 7,
            "short_laps": 2,
            "lapped": 1,
            "team_stop": 4,
            "on_progress": shell.update,
            "is_cancelled": shell.is_cancelled,
        }
    ]
    assert (shell.outcome, shell.dialog.ended) == (shell.presenter.outcome, [wx.ID_OK])


def test_on_go_given_an_out_of_range_lap_count_warns_and_keeps_the_dialog_open() -> None:
    """A refused race setting lands on the InfoBar and stays open."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    shell = _generator_shell(roster, laps=0, interval=1)

    SimulatorDialog._on_go(shell, None)

    assert shell.sim_infobar.messages == ["laps must be at least 1"]
    assert (shell.dialog.ended, shell.dialog.layouts) == ([], 1)
