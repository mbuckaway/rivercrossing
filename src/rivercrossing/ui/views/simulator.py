# SPDX-License-Identifier: GPL-3.0-only
"""``SimulatorDialog``/``SimRunningDialog``: the simulation dialogs.

The Rider Simulator fills an empty roster with placeholder riders, then
replays one scripted race through the live engine. Both halves of that
flow live here, each wrapping an already-XRC-loaded window with its
code-side behaviour:

* :class:`SimulatorDialog` reads the five count fields, seeds them
  from the persisted settings, derives the solo field from the riders
  and teams counts, forwards Generate Riders and Check to the
  presenter's rules, and runs the race through
  :class:`SimRunningDialog` when GO validates.
* :class:`SimRunningDialog` owns the progress gauge and the status
  line, and pumps the event loop so the gauge repaints and Cancel is
  dispatched while the race runs.

The two dialogs build their own :class:`~rivercrossing.ui.presenters.
simulator.SimulatorPresenter` over the engine and roster the app
threads in, mirroring ``views/selftest.py``'s presenter-inside-the-view
wiring: the view stays dumb, forwarding every control event straight to
the presenter, per module-skeletons.md's MVP split.

Code-side per ``simulation.xrc``'s own header footnote: ``sim_infobar``
(XRC cannot author a ``wxInfoBar``) is built with ``wx.InfoBar()`` and
given its frozen name with ``SetName()``, both show/hide effects off --
the measured hang ``results_win.py``/``main_frame.py`` record.

Plan §2's Check button is the one deliberate exception to the
InfoBar-first rule: it answers "do these counts agree?" with a native
OK-only ``std_dialogs.show_info``/``show_warning`` message box, because
the operator asked a question and the answer must not be missed.

**No threads.** The store and engine are main-thread-only, so the race
runs on the main thread inside the running dialog's nested event loop.
:meth:`SimRunningDialog.update` calls ``wx.Yield()`` after every
crossing so the gauge repaints and a Cancel click is delivered without
waiting for the race to end.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import wx
import wx.xrc

from rivercrossing.roster import MIN_TEAM_SIZE, EntryMode
from rivercrossing.ui import ids, std_dialogs
from rivercrossing.ui.presenters.simulator import (
    SimOutcome,
    SimulatorPresenter,
    check_message,
    default_interval_minutes,
    resolve_solo,
)
from rivercrossing.ui.views._support import DialogFindMixin, load_dialog

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.ride import RideEngine
    from rivercrossing.roster import Roster

__all__ = ["CHECK_TITLE", "MIN_WIDTH", "SIM_INFOBAR", "SimRunningDialog", "SimulatorDialog"]

# The setup dialog's width floor, measured on wxPython 4.3.1 /
# wxWidgets 3.3.3: the original two-generator row (123 + 6 + 122 +
# spacer + 74 + 6 + 74) plus the two 10px sizeritem borders = 425, and
# that row was wider than the label/field grid. Plan §1 retired the
# Generate Teams button and plan §2's Check button is narrower than it,
# so 425 stays the intended floor: it still clears the grid and never
# squeezes the Check, Generate and GO buttons (the live dialog's own
# best size measures 376 x 225).
MIN_WIDTH = 425

# The error InfoBar's frozen name (spec.md 15b). XRC cannot author a
# wxInfoBar at all (simulation.xrc's own header), so this name never
# appears in ui/ids.py -- the bar is built code-side and named with
# SetName(), mirroring main_frame.py's RESUME_INFOBAR precedent.
SIM_INFOBAR = "sim_infobar"

# The Check button's message-box caption (plan §2).
CHECK_TITLE = "Rider Simulator"


def _set_row_visible(control: wx.Window, *, visible: bool) -> None:
    """Show or hide *control*, its sizer slot and any label beside it.

    Hiding the control alone leaves its slot (and its static label)
    taking space in the frozen sizer, so the row is collapsed through
    the containing sizer too. A label is only hidden when it is the
    immediately-preceding sibling and a ``wx.StaticText`` -- the shape
    every labelled field in ``simulation.xrc`` uses (the same
    structural lookup ``rider_editor._set_team_choice_row_visible``
    performs).
    """
    sizer = control.GetContainingSizer()
    if sizer is not None:
        items = list(sizer.GetChildren())
        index = next((i for i, item in enumerate(items) if item.GetWindow() is control), -1)
        if index > 0:
            label = items[index - 1].GetWindow()
            if isinstance(label, wx.StaticText):
                sizer.Show(label, visible)
        sizer.Show(control, visible)
    control.Show(visible)


def _load_running_window(parent: wx.Window) -> wx.Dialog:
    """Load ``sim_running_dlg`` from the shared XRC resource.

    The app's own bootstrap fills the process-global ``XmlResource``
    (``app._load_xrc_resources``), so the runner loads from the same
    resource every other app-owned window comes from -- through
    :func:`~rivercrossing.ui.views._support.load_dialog`, so a
    degraded singleton load self-heals instead of answering ``None``.
    The *parent* is the modal ``simulation_dlg`` this dialog stacks
    over, and it survives the self-heal's rebuild.
    """
    return load_dialog(wx.xrc.XmlResource.Get(), ids.SIM_RUNNING_DLG, parent=parent)


class SimulatorDialog(DialogFindMixin):  # _find: ui.views._support
    """Code-side behaviour for ``simulation_dlg``.

    Seeds the count fields from the persisted settings, derives the
    solo field from the riders and teams counts, forwards Generate
    Riders to the presenter and runs the race through
    :class:`SimRunningDialog` on GO. Check (plan §2) explains the
    riders/teams/solo relationship in a system modal. A Generate the
    presenter refuses shows the refusal on ``sim_infobar`` and leaves
    the dialog open; a successful Generate closes it so the app
    persists the roster.

    :attr:`sim_values` is the five spins as last confirmed in
    :meth:`_on_generate_riders`/:meth:`_on_go`; the app reads it after
    the modal ends, when the real window is gone (plan §1).
    """

    # The five spin values, snapshotted before each successful close so
    # the app can persist them without touching the destroyed window.
    sim_values: tuple[int, int, int, int, int]

    def __init__(  # noqa: PLR0913 -- (dialog, engine, roster) + the seeds and the speed setting
        self,
        dialog: wx.Dialog,
        *,
        engine: RideEngine,
        roster: Roster,
        sim_riders: int = 175,
        sim_teams: int = 40,
        sim_solo: int = 15,
        sim_laps: int = 1,
        sim_interval: int = 45,
        avg_speed_kmh: float = 12.0,
    ) -> None:
        """Decorate an already-loaded ``simulation_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` the app bootstrap already
                loaded from ``simulation.xrc``.
            engine: The live ride engine the simulated crossings
                record into; its lap length sizes the interval seed.
            roster: The in-memory roster the generator fills.
            sim_riders: The persisted "Number of riders" seed.
            sim_teams: The persisted "Number of teams" seed.
            sim_solo: The persisted "Solo riders" seed, replaced on
                open by the count the two team fields imply.
            sim_laps: The persisted "Number of laps" seed.
            sim_interval: The persisted "Minutes between first rider"
                seed, used when no engine is threaded in.
            avg_speed_kmh: The operator's average rider speed, the
                setting the interval seed derives from.
        """
        self.dialog = dialog
        self.presenter = SimulatorPresenter(engine, roster)

        self.riders_spin = self._find(ids.RIDERS_SPIN, wx.SpinCtrl)
        self.teams_spin = self._find(ids.TEAMS_SPIN, wx.SpinCtrl)
        self.solo_spin = self._find(ids.SOLO_SPIN, wx.SpinCtrl)
        self.laps_spin = self._find(ids.LAPS_SPIN, wx.SpinCtrl)
        self.interval_spin = self._find(ids.INTERVAL_SPIN, wx.SpinCtrl)
        self.check_btn = self._find(ids.CHECK_BTN, wx.Button)
        self.gen_riders_btn = self._find(ids.GEN_RIDERS_BTN, wx.Button)
        self.go_btn = self._find(ids.GO_BTN, wx.Button)

        self._seed_spins(
            (sim_riders, sim_teams, sim_solo, sim_laps, sim_interval),
            avg_speed_kmh=avg_speed_kmh,
        )
        self._auto_solo()
        self._snapshot_sim_values()

        self.sim_infobar = self._build_infobar()

        self._bind_events()
        self._apply_roster_state()
        self.dialog.SetMinSize(wx.Size(MIN_WIDTH, -1))
        self.dialog.Fit()

    def _seed_spins(self, values: tuple[int, int, int, int, int], *, avg_speed_kmh: float) -> None:
        """Seed the five spins with *values* (plan §1/§3).

        ``wx.SpinCtrl.SetValue`` clamps to the control's own min/max, so
        a hand-edited settings file carrying an out-of-range count
        still opens on a usable field. The interval opens on the
        speed-derived default whenever an engine is threaded in -- the
        live ride's own lap length decides (plan §3) -- and falls back
        to the persisted value for a dialog with no engine (the plain
        XRC route). The keyword defaults mirror the XRC values, so an
        open with no seeds passed keeps them.
        """
        riders, teams, solo, laps, interval = values
        engine = self.presenter.engine
        if engine is not None:
            interval = default_interval_minutes(engine.config.lap_km, avg_speed_kmh)
        self.riders_spin.SetValue(riders)
        self.teams_spin.SetValue(teams)
        self.solo_spin.SetValue(solo)
        self.laps_spin.SetValue(laps)
        self.interval_spin.SetValue(interval)

    def _resolved_solo(self) -> int | None:
        """Return the solo count the two team spins imply (plan §1).

        ``None`` when the riders cannot fill the teams at all; the
        bounds are the ride's own (:data:`MIN_TEAM_SIZE` and the
        roster's ``max_team_size``), never copies of them.
        """
        return resolve_solo(
            self.riders_spin.GetValue(),
            self.teams_spin.GetValue(),
            min_team_size=MIN_TEAM_SIZE,
            max_team_size=self.presenter.roster.max_team_size,
        )

    def _auto_solo(self) -> None:
        """Fill ``solo_spin`` from the riders and teams fields (§1).

        Runs on every riders/teams change and on open. An impossible
        combination leaves the field untouched -- the problem surfaces
        through Check and Generate, never as a half-updated field. A
        SOLO-only ride has no teams to fill, so its hidden solo field
        stays as seeded.
        """
        if self.presenter.roster.entry_mode is EntryMode.SOLO:
            return
        solo = self._resolved_solo()
        if solo is not None:
            self.solo_spin.SetValue(solo)

    def _on_team_counts_changed(self, _event: wx.CommandEvent) -> None:
        """Recompute the solo field after a riders/teams change."""
        self._auto_solo()

    def _on_check(self, _event: wx.CommandEvent) -> None:
        """Show the riders/teams/solo relationship as a system modal."""
        self._show_check()

    def _show_check(self) -> None:
        """Explain the current counts in a native OK-only message box.

        Plan §2: the answer to "do these counts agree?" is a system
        modal -- :func:`~rivercrossing.ui.std_dialogs.show_info` when
        they do, ``show_warning`` when they cannot -- so a refusal is
        never a transient cue. Generate reuses it for the same reason.
        """
        riders = self.riders_spin.GetValue()
        teams = self.teams_spin.GetValue()
        message = check_message(
            riders,
            teams,
            min_team_size=MIN_TEAM_SIZE,
            max_team_size=self.presenter.roster.max_team_size,
        )
        show = (
            std_dialogs.show_info
            if self._resolved_solo() is not None
            else std_dialogs.show_warning
        )
        show(self.dialog, CHECK_TITLE, message)

    def _snapshot_sim_values(self) -> None:
        """Record the five spins for the app to persist (plan §1)."""
        self.sim_values = (
            self.riders_spin.GetValue(),
            self.teams_spin.GetValue(),
            self.solo_spin.GetValue(),
            self.laps_spin.GetValue(),
            self.interval_spin.GetValue(),
        )

    def _build_infobar(self) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Build the code-side :data:`SIM_INFOBAR`, inserted on top.

        Mirrors ``results_win.ResultsWindow._build_infobar``'s measured
        slide-effect hang fix: both effects disabled, so a later
        ``ShowMessage()`` returns. ``simulation.xrc`` reserves no
        InfoBar slot, so the bar is inserted at sizer index 0, above
        the generator grid.
        """
        bar = wx.InfoBar(self.dialog)
        bar.SetName(SIM_INFOBAR)
        bar.SetShowHideEffects(wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)
        self.dialog.GetSizer().Insert(0, bar, 0, wx.EXPAND)
        return bar

    def _bind_events(self) -> None:
        """Forward the generator controls to their handlers."""
        self.dialog.Bind(wx.EVT_BUTTON, self._on_generate_riders, self.gen_riders_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_check, self.check_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_go, self.go_btn)
        self.dialog.Bind(wx.EVT_SPINCTRL, self._on_team_counts_changed, self.riders_spin)
        self.dialog.Bind(wx.EVT_SPINCTRL, self._on_team_counts_changed, self.teams_spin)

    def _apply_roster_state(self) -> None:
        """Reflect the roster's entry mode and state on the fields.

        A SOLO-only ride has no teams to create, so ``teams_spin``,
        ``solo_spin`` and their Check button leave the dialog. A roster
        that already holds entries was loaded from a ride, so every
        generator control is disabled -- generating would collide with
        the real riders. The lap and interval fields stay live either
        way: simulating a loaded field is the point of GO.
        """
        if self.presenter.roster.entry_mode is EntryMode.SOLO:
            _set_row_visible(self.teams_spin, visible=False)
            _set_row_visible(self.solo_spin, visible=False)
            _set_row_visible(self.check_btn, visible=False)
        loaded = bool(self.presenter.roster.entries)
        for control in (
            self.riders_spin,
            self.teams_spin,
            self.solo_spin,
            self.check_btn,
            self.gen_riders_btn,
        ):
            control.Enable(not loaded)

    def _on_generate_riders(self, _event: wx.CommandEvent) -> None:
        """Generate the requested riders, then close on success.

        A MIXED ride resolves the solo count from the riders and teams
        fields first (plan §1), writes it back to ``solo_spin`` and
        generates -- the operator's stale or absent solo value never
        blocks a valid field. A riders/teams pair that cannot fill the
        teams shows the Check explanation instead. A SOLO-only ride
        generates solo entries only through
        :meth:`generate_solo_riders`.
        """
        if self.presenter.roster.entry_mode is EntryMode.SOLO:
            self._generate(
                lambda: self.presenter.generate_solo_riders(self.riders_spin.GetValue())
            )
            return
        solo = self._resolved_solo()
        if solo is None:
            self._show_check()
            return
        self.solo_spin.SetValue(solo)
        riders = self.riders_spin.GetValue()
        teams = self.teams_spin.GetValue()
        self._generate(lambda: self.presenter.generate_riders(riders, teams, solo))

    def _generate(self, generate: Callable[[], None]) -> None:
        """Run *generate*; close on success, warn on a refused count.

        The presenter refuses a count combination outside the team-size
        bounds with a ``ValueError``; the message is the operator's
        explanation, so it lands on ``sim_infobar`` and the dialog stays
        open for a correction. A successful generation snapshots the
        spins before closing (plan §1).
        """
        try:
            generate()
        except ValueError as exc:
            self.sim_infobar.ShowMessage(str(exc), wx.ICON_WARNING)
            self.dialog.Layout()
            return
        self._snapshot_sim_values()
        self.dialog.EndModal(wx.ID_OK)

    def _on_go(self, _event: wx.CommandEvent) -> None:
        """Validate the race settings, then run the race."""
        laps = self.laps_spin.GetValue()
        interval_minutes = self.interval_spin.GetValue()
        refusal = self.presenter.validate(laps=laps, interval_minutes=interval_minutes)
        if refusal is not None:
            self.sim_infobar.ShowMessage(refusal, wx.ICON_WARNING)
            self.dialog.Layout()
            return
        running = SimRunningDialog(
            _load_running_window(self.dialog),
            engine=self.presenter.engine,
            roster=self.presenter.roster,
            laps=laps,
            interval_minutes=interval_minutes,
        )
        running.run()
        self._snapshot_sim_values()
        self.dialog.EndModal(wx.ID_OK)


class SimRunningDialog(DialogFindMixin):  # _find: ui.views._support
    """Code-side behaviour for ``sim_running_dlg``.

    Owns the progress gauge, the status line and Cancel, and drives one
    :meth:`~rivercrossing.ui.presenters.simulator.SimulatorPresenter.
    run_simulation` call. The race runs on the main thread inside this
    dialog's own modal loop; :meth:`update` yields after every crossing
    so the dialog stays responsive.
    """

    def __init__(  # noqa: PLR0913 -- (dialog, engine, roster) + the race settings
        self,
        dialog: wx.Dialog,
        *,
        engine: RideEngine,
        roster: Roster,
        laps: int,
        interval_minutes: int,
    ) -> None:
        """Decorate an already-loaded ``sim_running_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` loaded from ``simulation.xrc``.
            engine: The live ride engine the crossings record into.
            roster: The roster the simulation races.
            laps: How many laps to simulate.
            interval_minutes: The minutes between one lap and the next.
        """
        self.dialog = dialog
        self.presenter = SimulatorPresenter(engine, roster)
        self.laps = laps
        self.interval_minutes = interval_minutes
        self._cancelled = False
        self.outcome: SimOutcome | None = None

        self.progress_gauge = self._find(ids.PROGRESS_GAUGE, wx.Gauge)
        self.sim_status_lbl = self._find(ids.SIM_STATUS_LBL, wx.StaticText)
        self.cancel_btn = self._find(ids.CANCEL_BTN, wx.Button)

        # wx's native Escape handling only ever looks for wxID_CANCEL,
        # and cancel_btn carries a custom id, so both the escape id and
        # the handler are set explicitly -- Escape then lands on the
        # same cancel path a click does (dialogs.wire_escape_to's own
        # measured reasoning).
        self.dialog.SetEscapeId(self.cancel_btn.GetId())
        self.dialog.Bind(wx.EVT_BUTTON, self._on_cancel, self.cancel_btn)
        self.dialog.Fit()

    def _on_cancel(self, _event: wx.CommandEvent) -> None:
        """Record the cancel request; the run loop polls it."""
        self._cancelled = True

    def is_cancelled(self) -> bool:
        """Return whether the operator has asked to end the run."""
        return self._cancelled

    def update(self, completed: int, total: int) -> None:
        """Show progress and pump the loop so the gauge repaints.

        Called after every recorded crossing. ``wx.Yield()`` runs the
        pending events -- the gauge paint and any Cancel click -- while
        the race loop holds the main thread.
        """
        self.progress_gauge.SetValue(100 * completed // total)
        self.sim_status_lbl.SetLabel(f"Simulated {completed} of {total} crossings")
        wx.Yield()

    def run(self) -> SimOutcome | None:
        """Show the dialog modally and return the race's outcome.

        The race is started with ``wx.CallAfter`` so it runs inside the
        modal loop this call opens, then ``ShowModal`` blocks until
        :meth:`_run` ends the modal.
        """
        wx.CallAfter(self._run)
        self.dialog.ShowModal()
        return self.outcome

    def _run(self) -> None:
        """Run the simulation, then close the running dialog."""
        self.outcome = self.presenter.run_simulation(
            self.laps,
            self.interval_minutes,
            on_progress=self.update,
            is_cancelled=self.is_cancelled,
        )
        self.dialog.EndModal(wx.ID_OK)
