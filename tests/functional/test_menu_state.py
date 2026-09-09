# SPDX-License-Identifier: GPL-3.0-only
"""Real-widget R-35 arm-gate check for the console (E1.4.2, R-35).

The full item x state enablement matrix, every named ``Enablement``
condition's boundary/negative cases, and ``is_stop_button_enabled``
(R-35) are pure Python against ``commands.py`` -- no ``wx`` involved
-- so they live in ``tests/unit/ui/test_commands.py`` instead
(mirroring the split ``cards_imagelist`` already uses). What needs a
real, loaded ``main_frame`` is the authored-XRC fact plus the wiring
between the authored control and the code-side rule:

* ``arm_stop_chk`` is genuinely unticked by default in the authored
  XRC -- R-35's precondition (the module's original pin).
* Ticking it through a real ``EVT_CHECKBOX`` reaches
  ``MainFrame.wire_console``'s binding, whose handler reads the
  checkbox and calls ``ConsolePresenter.on_arm_stop``; the presenter
  routes ``set_stop_enabled`` back to the view, and the view enables
  ``stop_btn`` -- R-35's first deliberate act -- while the
  ride-state label does not move ("Stop is a UI guard, not a state",
  spec §3). A binding that was dead, or a view that ignored it,
  would fail the assertions below.

The single window is wired exactly as the app bootstrap wires it
(``wire_console`` + ``set_state`` over ``app._build_console_engine``),
so the drive exercises the real controls, never the presenter in
isolation.
"""

from typing import Any

import harness
import pytest
import wx

from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui import commands, ids
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.views import MainFrame

pytestmark = pytest.mark.functional


def _tick_arm_stop_checkbox(frame: Any, *, value: bool) -> None:  # noqa: ANN401
    """Set ``arm_stop_chk`` and post the ``EVT_CHECKBOX`` a click fires.

    ``harness`` has helpers for buttons, choices and radios but not
    checkboxes; a plain ``SetValue`` fires no ``EVT_CHECKBOX`` (the
    same silence its other helpers document), so the event is posted
    directly -- the sibling ``_set_checkbox`` idiom in
    ``console_subprocess_scenarios.py`` and ``test_lists_results.py``.
    """
    control = harness.find_control(frame, ids.ARM_STOP_CHK)
    control.SetValue(value)
    event = wx.CommandEvent(wx.EVT_CHECKBOX.typeId, control.GetId())
    event.SetEventObject(control)
    control.GetEventHandler().ProcessEvent(event)
    harness.pump()


def test_stop_button_tracks_arm_stop_chk_from_xrc_default_through_a_wired_toggle(
    xrc_resource: object,
) -> None:
    """R-35: Arm ticks in XRC, then the wired toggle drives Stop.

    Reads the authored default before any code runs, then builds the
    real console over the same frame and drives the checkbox both
    ways through the binding ``wire_console`` installs.
    """
    frame = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    console = None
    presenter = None
    try:
        arm_chk = harness.find_control(frame, ids.ARM_STOP_CHK)

        # Phase 1 -- the authored XRC default (this module's original
        # pin): unticked, so the pure command rule says Stop is off.
        assert arm_chk.GetValue() is False
        assert commands.is_stop_button_enabled(armed=arm_chk.GetValue()) is False

        # Phase 2 -- wire the real console over the same frame exactly
        # as the app bootstrap does: production engine, presenter,
        # ``wire_console`` bindings, initial state render. The code
        # also gates Stop at construction even if XRC ever left it on.
        roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
        engine, source = app_module._build_console_engine(roster)
        console = MainFrame(frame, data_source=source, resource=xrc_resource)
        presenter = ConsolePresenter(console, engine=engine, source=source)
        console.wire_console(presenter)
        console.set_state(source.ride_status())
        stop_btn = harness.find_control(frame, ids.STOP_BTN)
        status_lbl = harness.find_control(frame, ids.RIDE_STATUS_LBL)
        assert (stop_btn.IsEnabled(), status_lbl.GetLabelText()) == (False, "DRAFT")

        # Phase 3 -- arm through a real EVT_CHECKBOX. The binding's
        # handler reads the checkbox and the presenter enables Stop.
        _tick_arm_stop_checkbox(frame, value=True)
        assert (arm_chk.GetValue(), stop_btn.IsEnabled()) == (True, True)

        # R-35 is a UI guard, not a state: arming must not move the
        # ride-status label.
        assert status_lbl.GetLabelText() == "DRAFT"

        # Phase 4 -- Show + pump, then read the rendered controls:
        # both carry their assistive-tech labels (R-83) with the
        # enabled/checked state the drive produced.
        frame.Show()
        frame.Layout()
        harness.pump()
        assert (arm_chk.GetLabelText(), arm_chk.GetValue()) == ("Arm", True)
        assert (stop_btn.GetLabelText(), stop_btn.IsEnabled()) == ("Stop ride…", True)
        assert status_lbl.GetLabelText() == "DRAFT"

        # Phase 5 -- disarm round trip: the same binding turns Stop
        # back off, so a one-way (sticky) handler would fail here.
        _tick_arm_stop_checkbox(frame, value=False)
        assert (arm_chk.GetValue(), stop_btn.IsEnabled()) == (False, False)
    finally:
        # Phase-2 reference hygiene (the shared-fixture precedent):
        # drop the view and presenter before the window dies so
        # close_window's final gc.collect() can evict their wrappers.
        del console, presenter
        harness.close_window(frame)
