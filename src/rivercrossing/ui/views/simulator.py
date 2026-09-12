# SPDX-License-Identifier: GPL-3.0-only
"""``SimulatorDialog``/``SimRunningDialog``: the simulation dialogs.

The Rider Simulator fills an empty roster with placeholder riders, then
replays one scripted race through the live engine. Both halves of that
flow live here, each wrapping an already-XRC-loaded window with its
code-side behaviour:

* :class:`SimulatorDialog` reads the five count fields, forwards the
  two Generate buttons to the presenter, and runs the race through
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

from rivercrossing.roster import EntryMode
from rivercrossing.ui import ids
from rivercrossing.ui.presenters.simulator import SimOutcome, SimulatorPresenter
from rivercrossing.ui.views._support import find_control

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.ride import RideEngine
    from rivercrossing.roster import Roster

__all__ = ["MIN_WIDTH", "SIM_INFOBAR", "SimRunningDialog", "SimulatorDialog"]

# The setup dialog's width floor, measured on wxPython 4.3.1 /
# wxWidgets 3.3.3: Fit() reports the generator row (123 + 6 + 122 +
# spacer + 74 + 6 + 74) plus the two 10px sizeritem borders = 425, and
# that row is wider than the label/field grid. Below 425 the Generate
# and GO buttons would be squeezed; the measured best width is the
# floor.
MIN_WIDTH = 425

# The error InfoBar's frozen name (spec.md 15b). XRC cannot author a
# wxInfoBar at all (simulation.xrc's own header), so this name never
# appears in ui/ids.py -- the bar is built code-side and named with
# SetName(), mirroring main_frame.py's RESUME_INFOBAR precedent.
SIM_INFOBAR = "sim_infobar"


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
    resource every other app-owned window comes from.
    """
    return wx.xrc.XmlResource.Get().LoadDialog(parent, ids.SIM_RUNNING_DLG)


class SimulatorDialog:
    """Code-side behaviour for ``simulation_dlg``.

    Reads the five count fields, forwards Generate to the presenter and
    runs the race through :class:`SimRunningDialog` on GO. A Generate
    that the presenter refuses (a count combination outside the team
    bounds) shows the refusal on ``sim_infobar`` and leaves the dialog
    open; a successful Generate closes it so the app persists the
    roster.
    """

    def __init__(self, dialog: wx.Dialog, *, engine: RideEngine, roster: Roster) -> None:
        """Decorate an already-loaded ``simulation_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``simulation.xrc``.
            engine: The live ride engine the simulated crossings
                record into.
            roster: The in-memory roster the generator fills.
        """
        self.dialog = dialog
        self.presenter = SimulatorPresenter(engine, roster)

        self.riders_spin = self._find(ids.RIDERS_SPIN, wx.SpinCtrl)
        self.teams_spin = self._find(ids.TEAMS_SPIN, wx.SpinCtrl)
        self.solo_spin = self._find(ids.SOLO_SPIN, wx.SpinCtrl)
        self.laps_spin = self._find(ids.LAPS_SPIN, wx.SpinCtrl)
        self.interval_spin = self._find(ids.INTERVAL_SPIN, wx.SpinCtrl)
        self.gen_teams_btn = self._find(ids.GEN_TEAMS_BTN, wx.Button)
        self.gen_riders_btn = self._find(ids.GEN_RIDERS_BTN, wx.Button)
        self.go_btn = self._find(ids.GO_BTN, wx.Button)

        self.sim_infobar = self._build_infobar()

        self._bind_events()
        self._apply_roster_state()
        self.dialog.SetMinSize(wx.Size(MIN_WIDTH, -1))
        self.dialog.Fit()

    def _find(self, name: str, expected_type: type = wx.Window) -> Any:  # noqa: ANN401
        """Resolve one of this dialog's own child controls by name.

        See :func:`find_control`'s docstring (``ui.views._support``)
        for the full measured reasoning this mirrors.

        Raises:
            LookupError: If *name* does not resolve to an
                *expected_type* instance inside this dialog, even
                after settling.
        """
        return find_control(self.dialog, name, expected_type)

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
        """Forward the two Generate buttons and GO to their handlers."""
        self.dialog.Bind(wx.EVT_BUTTON, self._on_generate_teams, self.gen_teams_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_generate_riders, self.gen_riders_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_go, self.go_btn)

    def _apply_roster_state(self) -> None:
        """Reflect the roster's entry mode and state on the fields.

        A SOLO-only ride has no teams to create, so ``teams_spin``,
        ``solo_spin`` and ``gen_teams_btn`` leave the dialog. A roster
        that already holds entries was loaded from a ride, so every
        generator control is disabled -- generating would collide with
        the real riders. The lap and interval fields stay live either
        way: simulating a loaded field is the point of GO.
        """
        if self.presenter.roster.entry_mode is EntryMode.SOLO:
            _set_row_visible(self.teams_spin, visible=False)
            _set_row_visible(self.solo_spin, visible=False)
            _set_row_visible(self.gen_teams_btn, visible=False)
        loaded = bool(self.presenter.roster.entries)
        for control in (
            self.riders_spin,
            self.teams_spin,
            self.solo_spin,
            self.gen_teams_btn,
            self.gen_riders_btn,
        ):
            control.Enable(not loaded)

    def _on_generate_teams(self, _event: wx.CommandEvent) -> None:
        """Generate the requested empty teams, then close on success."""
        self._generate(lambda: self.presenter.generate_teams(self.teams_spin.GetValue()))

    def _on_generate_riders(self, _event: wx.CommandEvent) -> None:
        """Generate the requested riders, then close on success.

        A MIXED ride generates solo entries plus team riders through
        :meth:`generate_riders`; a SOLO-only ride generates solo entries
        only through :meth:`generate_solo_riders`.
        """
        solo_only = self.presenter.roster.entry_mode is EntryMode.SOLO
        if solo_only:
            self._generate(
                lambda: self.presenter.generate_solo_riders(self.riders_spin.GetValue())
            )
            return
        self._generate(
            lambda: self.presenter.generate_riders(
                self.riders_spin.GetValue(),
                self.teams_spin.GetValue(),
                self.solo_spin.GetValue(),
            )
        )

    def _generate(self, generate: Callable[[], None]) -> None:
        """Run *generate*; close on success, warn on a refused count.

        The presenter refuses a count combination outside the team-size
        bounds with a ``ValueError``; the message is the operator's
        explanation, so it lands on ``sim_infobar`` and the dialog stays
        open for a correction.
        """
        try:
            generate()
        except ValueError as exc:
            self.sim_infobar.ShowMessage(str(exc), wx.ICON_WARNING)
            self.dialog.Layout()
            return
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
        self.dialog.EndModal(wx.ID_OK)


class SimRunningDialog:
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

    def _find(self, name: str, expected_type: type = wx.Window) -> Any:  # noqa: ANN401
        """Resolve one of this dialog's own child controls by name.

        Raises:
            LookupError: If *name* does not resolve to an
                *expected_type* instance inside this dialog, even
                after settling.
        """
        return find_control(self.dialog, name, expected_type)

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
