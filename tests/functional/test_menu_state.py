# SPDX-License-Identifier: GPL-3.0-only
"""Real-widget C2 stop-flow check for the console (R-35, W5).

The full item x state enablement matrix and every named ``Enablement``
condition's boundary/negative cases live in
``tests/unit/ui/test_commands.py`` (no ``wx`` involved); what needs a
real, loaded ``main_frame`` is the authored-XRC fact plus the wiring
between the control and the code-side rule the presenter owns:

* ``stop_btn`` is disabled in DRAFT and enabled the moment the ride is
  live RUNNING -- C2 retired the Arm checkbox, so Stop is one act (the
  presenter's ``refresh_console_gates`` is the single source, and it
  re-applies the gate on every state render).
* Clicking it asks through the native ``std_dialogs.show_confirm``
  (W5; the same flow Ride ▸ Stop Ride… runs) and a confirmed OK calls
  ``engine.stop()``: plate entry locks while the ride stays RUNNING
  ("Stop is a UI guard, not a state", spec §3), the label reads
  STOPPED and Stop turns off.
* A cancelled confirm changes nothing, and Start (continue) re-enables
  entry with the original start kept -- the one-act Stop pattern
  ``tests/acceptance/race_child.py``'s ``_stop_and_continue``
  established.

The single window is wired exactly as the app bootstrap wires it
(``wire_console`` + ``set_state`` over ``app._build_console_engine``),
so the drive exercises the real controls, never the presenter in
isolation.
"""

import contextlib
from typing import TYPE_CHECKING, Any

import harness
import pytest
import wx

from rivercrossing.ride import RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui import ids, std_dialogs
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.views import MainFrame

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.functional


@contextlib.contextmanager
def _native_confirm(result: int = wx.ID_OK) -> Iterator[None]:
    """Answer the native stop confirm with *result* (race_child's seam).

    The stop confirm is the native ``wx.MessageDialog`` behind
    ``std_dialogs.show_confirm``, which this harness cannot dismiss
    programmatically (measured 2026-09-09: a native message dialog has
    no wx children), so the module function is swapped for the route's
    own synchronous handler and restored afterwards -- the same seam
    ``tests/acceptance/race_child.py``'s ``_native_confirm`` uses.
    """
    original = std_dialogs.show_confirm
    std_dialogs.show_confirm = lambda *_args, **_kwargs: result
    try:
        yield
    finally:
        std_dialogs.show_confirm = original


def _build_console(frame: Any) -> tuple[MainFrame, ConsolePresenter]:  # noqa: ANN401 -- wx ships no stubs
    """Build the app's own DRAFT console over *frame*."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Rider 12", last_name="", plate="12")
    engine, source = app_module._build_console_engine(roster)
    console = MainFrame(frame, data_source=source)
    presenter = ConsolePresenter(console, engine=engine, source=source)
    console.wire_console(presenter)
    console.set_state(source.ride_status())
    return console, presenter


def test_stop_button_one_act_confirm_locks_entry_and_start_reenables(
    xrc_resource: object,
) -> None:
    """C2: DRAFT gates Stop off; RUNNING enables it; OK locks entry."""
    frame = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    console = None
    presenter = None
    try:
        console, presenter = _build_console(frame)
        stop_btn = harness.find_control(frame, ids.STOP_BTN)
        plate_input = harness.find_control(frame, ids.PLATE_INPUT)
        status_lbl = harness.find_control(frame, ids.RIDE_STATUS_LBL)

        # Phase 1 -- DRAFT: Stop is off with nothing to stop.
        assert (stop_btn.IsEnabled(), status_lbl.GetLabelText()) == (False, "DRAFT")

        # Phase 2 -- start through the real start_btn binding: the
        # live-RUNNING gate is what enables the one stop act.
        harness.click(frame, ids.START_BTN)
        assert (stop_btn.IsEnabled(), status_lbl.GetLabelText()) == (True, "RUNNING")

        # Phase 3 -- the confirmed stop: entry locks, the label reads
        # STOPPED and the gate turns Stop back off.
        with _native_confirm(wx.ID_OK):
            harness.click(frame, ids.STOP_BTN)
            harness.pump()

        engine = presenter.engine
        assert engine.stopped is True
        assert engine.state is RideStatus.RUNNING  # stop is a guard, not a state
        assert plate_input.IsEnabled() is False
        assert stop_btn.IsEnabled() is False
        assert status_lbl.GetLabelText() == "STOPPED"

        # Phase 4 -- continue: entry re-enabled, the original start
        # kept (the race_child _stop_and_continue comparison).
        harness.click(frame, ids.START_BTN)
        assert plate_input.IsEnabled() is True
        assert status_lbl.GetLabelText() == "RUNNING"
        start_payload = next(e.payload for e in engine.events if e.action == "start")
        continue_payload = next(e.payload for e in engine.events if e.action == "continue")
        assert continue_payload["actual_start"] == start_payload["actual_start"]
    finally:
        # Reference hygiene (the shared-fixture precedent): drop the
        # view and presenter before the window dies.
        del console, presenter
        harness.close_window(frame)


def test_stop_button_cancelled_confirm_leaves_entry_live(
    xrc_resource: object,
) -> None:
    """R-35's guard: a cancelled stop confirm changes nothing."""
    frame = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    console = None
    presenter = None
    try:
        console, presenter = _build_console(frame)
        harness.click(frame, ids.START_BTN)
        stop_btn = harness.find_control(frame, ids.STOP_BTN)
        plate_input = harness.find_control(frame, ids.PLATE_INPUT)

        with _native_confirm(wx.ID_CANCEL):
            harness.click(frame, ids.STOP_BTN)
            harness.pump()

        assert presenter.engine.stopped is False
        assert plate_input.IsEnabled() is True
        assert stop_btn.IsEnabled() is True
        assert harness.find_control(frame, ids.RIDE_STATUS_LBL).GetLabelText() == "RUNNING"
    finally:
        del console, presenter
        harness.close_window(frame)
