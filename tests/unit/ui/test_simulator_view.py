# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the Rider Simulator dialog's control state.

Only what genuinely needs no window is pinned here, in the
``test_results_win.py`` / ``test_rider_editor_dialog.py`` style:
:meth:`SimulatorDialog._apply_roster_state` driven against a shell that
owns only the five generator controls and the presenter's roster. The
live layout -- real spin controls on a real dialog -- stays with the
(functional) suite.
"""

from __future__ import annotations

from rivercrossing.roster import EntryMode
from rivercrossing.ui.views.simulator import SimulatorDialog


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
    """A SimulatorDialog shell owning only the five controls."""

    def __init__(self, *, entry_mode: EntryMode, loaded: bool) -> None:
        """Build the roster double and one double per control."""
        self.presenter = _Presenter(_Roster(entry_mode=entry_mode, loaded=loaded))
        self.riders_spin = _Control()
        self.teams_spin = _Control()
        self.solo_spin = _Control()
        self.gen_teams_btn = _Control()
        self.gen_riders_btn = _Control()


def test_apply_roster_state_given_loaded_roster_disables_every_generator_control() -> None:
    """A loaded roster disables every generator control."""
    shell = _Shell(entry_mode=EntryMode.MIXED, loaded=True)

    SimulatorDialog._apply_roster_state(shell)

    assert [
        shell.riders_spin.enabled,
        shell.teams_spin.enabled,
        shell.solo_spin.enabled,
        shell.gen_teams_btn.enabled,
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
        shell.gen_teams_btn.enabled,
        shell.gen_riders_btn.enabled,
    ] == [True, True, True, True, True]


def test_apply_roster_state_given_solo_mode_hides_the_team_and_solo_fields() -> None:
    """A SOLO-only ride hides the team fields."""
    shell = _Shell(entry_mode=EntryMode.SOLO, loaded=False)

    SimulatorDialog._apply_roster_state(shell)

    assert (shell.teams_spin.shown, shell.solo_spin.shown, shell.gen_teams_btn.shown) == (
        False,
        False,
        False,
    )
    assert shell.riders_spin.shown is True


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

    assert (shell.teams_spin.shown, shell.solo_spin.shown, shell.gen_teams_btn.shown) == (
        True,
        True,
        True,
    )
