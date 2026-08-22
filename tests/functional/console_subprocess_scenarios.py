# SPDX-License-Identifier: GPL-3.0-only
"""Child-process program for this suite's subprocess-isolated scenarios.

Constructing many ``MainFrame``s across one long pytest session
measurably raises a wxPython 4.3.1/wxWidgets 3.3.3 address-reuse
hazard (``views/main_frame.py``'s own ``_find`` docstring): a
freshly-allocated control can transiently answer ``FindWindowByName``
with a *different*, already-destroyed control's Python type. Running
each widget-churn-heavy scenario in a brand-new, **spawned**
interpreter -- never forked, since forking a process that may already
have an initialised ``NSApplication`` is unsafe on macOS, and this
session's own ``wx_app`` fixture usually already has one -- removes
the *accumulated* churn a long, shared session builds up.

That alone is not the whole story for the three original scenarios
(``sash_round_trip``, ``hide_times_columns_round_trip``,
``hide_times_leaves_clock_shown``), measured with throwaway sampling
scripts per this repo's convention: even fully isolated, one attempt
at the sash-round-trip sequence specifically (build, SetSashPosition,
persist, destroy, rebuild, GetSashPosition -- the one of the three
that touches ``main_splitter`` twice) still hits the hazard at a real
per-attempt rate (roughly one attempt in six, sampled). The other two
measured clean at 20/20 single-attempt runs and stay as one attempt
each. :func:`_sash_round_trip` retries the *whole sequence* -- not a
single lookup, which ``main_frame.py``'s own ``_find`` already does
without it helping once one lookup inside a construction has gone
wrong -- which cuts the residual rate sharply but, sampled, does not
always reach zero within one process: a rare process launch can land
on a layout where every in-process attempt fails alike.
``scenario_runner.run_scenario`` adds a second layer on top of this
module, retrying the *spawn* itself (a fresh process gets an
independent layout) -- the two together are what the full-suite
measurement in this task's report is of.

The Phase 8 scenarios below (``plate_entry_round_trip``,
``record_btn_click_records_once``, ``console_starts_in_running_state``,
``state_enablement_round_trip``) isolate for a second reason on top of
the same address-reuse motivation: each mutates ``main_frame`` state
(enablement, the status bar, the entry field, or a real
``EVT_TEXT_ENTER``/``EVT_BUTTON`` dispatch through the full app
bootstrap) that ``test_console_demo.py``'s shared, read-only
``shared_console`` fixture explicitly forbids mutating.

Task 8.5's quit-flow scenarios (``quit_menu_confirmed_destroys`` and
its siblings, called from ``test_quit_flow_wx.py`` rather than
``test_console_demo.py``) isolate for a third reason: quitting is
process-global state -- ``wx.App.really_quitting``, and the red
X/Dock-reopen pair that only ever makes sense against one live
``main_frame`` at a time -- so each gets its own fresh interpreter
too, never sharing one with any other scenario.

Phase 10, measured on windows-latest CI run 31015653629: every
scenario above that builds ``main_frame`` through
:func:`rivercrossing.ui.app.build_main_window` and then cleans it up
with a plain, vetoable ``harness.close_window(frame)`` hangs on
Windows. A vetoable ``Close()`` there runs the very same
``_confirm_quit`` flow File ▸ Exit does (``app.py``'s
``_on_main_frame_close``, non-mac branch) -- macOS vetoes and hides
instead -- and nothing in the child dismisses the resulting modal, so
the parent's 30s timeout kills the child before this module's own
``print(json.dumps(result))`` ever reaches the pipe (empty
stdout/stderr, ``data=None``, the exact symptom that CI run showed).
:func:`_close_without_prompt` closes the gap: setting
``really_quitting`` first makes the same guard
:func:`~rivercrossing.ui.app._handle_exit_route`'s own forced close
already relies on destroy the frame immediately, with no dialog, on
every platform.

This module *is* the child process's entire program. Run as::

    python console_subprocess_scenarios.py <scenario>

it builds its own app (:func:`rivercrossing.ui.app.build_app`) and
its own XRC resource (nothing from the parent session's fixtures
crosses a process boundary), runs one scenario, and prints exactly
one JSON line to stdout::

    {"ok": bool, "error": str | None, "data": {...} | None}

It never asserts anything itself -- the caller test module decodes
this line and performs the actual comparisons, so a wrong measured
value still surfaces as a normal pytest assertion diff, not a bare
non-zero exit code. ``faulthandler.enable()`` runs first, before
anything else in :func:`main`, so a native-level crash (a segfault,
not a Python exception) still writes a Python traceback to stderr
instead of leaving the parent with nothing but a bare non-zero exit
code to diagnose -- the same empty-pipe failure mode
:func:`_close_without_prompt` above exists to prevent for the hang
case.
"""

import faulthandler
import json
import os
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import harness
import pages
import scenario_runner
import wx
import wx.xrc

from rivercrossing.demo import DemoDataSource
from rivercrossing.ride import RideStatus
from rivercrossing.ui import app as app_module
from rivercrossing.ui import ids, theme
from rivercrossing.ui.views import MainFrame, dialogs

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["main"]

_SCREENSHOT_DIR = Path(__file__).resolve().parent / "_screenshots"


def _visible_column_titles(crossings_list: Any) -> list[str]:  # noqa: ANN401
    """Return the titles of every non-hidden column, in column order."""
    return [
        crossings_list.GetColumn(index).GetTitle()
        for index in range(crossings_list.GetColumnCount())
        if not crossings_list.GetColumn(index).IsHidden()
    ]


_SASH_ROUND_TRIP_ATTEMPTS = 5


def _sash_round_trip_once(resource: Any) -> dict[str, Any]:  # noqa: ANN401
    """One attempt at the sash round-trip; may raise ``LookupError``."""
    first_window = harness.load_window(resource, ids.MAIN_FRAME, frame=True)
    first_window.Show()
    first_window.Layout()
    harness.pump()
    first_console = MainFrame(first_window, data_source=DemoDataSource())
    first_console.main_splitter.SetSashPosition(300)
    first_console.persist_layout()
    harness.close_window(first_window)

    second_window = harness.load_window(resource, ids.MAIN_FRAME, frame=True)
    second_window.Show()
    second_window.Layout()
    harness.pump()
    try:
        second_console = MainFrame(second_window, data_source=DemoDataSource())
        restored = second_console.main_splitter.GetSashPosition()
    finally:
        harness.close_window(second_window)

    return {"restored_sash": restored}


def _sash_round_trip() -> dict[str, Any]:
    """Persist a sash position, rebuild fresh, read it back (see above).

    Retries the whole sequence: the first attempt that raises no
    ``LookupError`` wins.
    """
    resource = harness.load_xrc_resources()
    last_error: LookupError | None = None
    for _attempt in range(_SASH_ROUND_TRIP_ATTEMPTS):
        try:
            return _sash_round_trip_once(resource)
        except LookupError as exc:
            last_error = exc
    assert last_error is not None  # every iteration above sets it
    raise last_error


def _hide_times_columns_round_trip() -> dict[str, Any]:
    """Hide, then restore, the Lap time/Total columns both ways."""
    resource = harness.load_xrc_resources()
    window = harness.load_window(resource, ids.MAIN_FRAME, frame=True)
    window.Show()
    window.Layout()
    harness.pump()
    try:
        console = MainFrame(window, data_source=DemoDataSource())
        crossings_list = harness.find_control(window, ids.CROSSINGS_LIST)
        before = _visible_column_titles(crossings_list)
        console.set_hide_times(hide=True)
        during = _visible_column_titles(crossings_list)
        console.set_hide_times(hide=False)
        after = _visible_column_titles(crossings_list)
    finally:
        harness.close_window(window)
    return {"before": before, "during": during, "after": after}


def _hide_times_leaves_clock_shown() -> dict[str, Any]:
    """Toggle hide-times on; read whether the clock labels stay up."""
    resource = harness.load_xrc_resources()
    window = harness.load_window(resource, ids.MAIN_FRAME, frame=True)
    window.Show()
    window.Layout()
    harness.pump()
    try:
        console = MainFrame(window, data_source=DemoDataSource())
        console.set_hide_times(hide=True)
        elapsed_shown = harness.find_control(window, ids.CLOCK_ELAPSED_LBL).IsShown()
        remaining_shown = harness.find_control(window, ids.CLOCK_REMAINING_LBL).IsShown()
    finally:
        harness.close_window(window)
    return {"clock_elapsed_shown": elapsed_shown, "clock_remaining_shown": remaining_shown}


def _state_enablement_round_trip() -> dict[str, Any]:
    """set_state enables plate_input/record_btn together, both ways."""
    resource = harness.load_xrc_resources()
    window = harness.load_window(resource, ids.MAIN_FRAME, frame=True)
    window.Show()
    window.Layout()
    harness.pump()
    try:
        console = MainFrame(window, data_source=DemoDataSource())
        record_btn = harness.find_control(window, ids.RECORD_BTN)
        console.set_state(RideStatus.RUNNING)
        running = [console.plate_input.IsEnabled(), record_btn.IsEnabled()]
        console.set_state(RideStatus.DRAFT)
        draft = [console.plate_input.IsEnabled(), record_btn.IsEnabled()]
    finally:
        harness.close_window(window)
    return {"running": running, "draft": draft}


def _counting_show_notices() -> tuple[list[str], Callable[[], None]]:
    """Wrap ``MainFrame.show_notice`` to count its real calls (A5).

    # logic-coverage-exempt: T-10 -- this passthrough spy targets an
    # internal view method, not a true I/O boundary, because the
    # final rendered status-bar text alone cannot distinguish one
    # real call from a wrongly re-fired second one:
    # on_plate_entered's own blank-guard swallows a double-fire's
    # visible side effects (its second call would see an
    # already-cleared field and only refocus, leaving the status
    # bar's text unchanged either way). Counting real invocations is
    # the only direct way to observe the double-submit guard; the
    # spy still calls through to the genuine implementation, it
    # never fakes it.
    """
    calls: list[str] = []
    original = MainFrame.show_notice

    def _counting(self: MainFrame, text: str) -> None:
        calls.append(text)
        original(self, text)

    MainFrame.show_notice = _counting

    def _restore() -> None:
        MainFrame.show_notice = original

    return calls, _restore


def _post_text_enter(control: Any) -> None:  # noqa: ANN401
    """Post the event a real Enter keypress fires in *control*."""
    event = wx.CommandEvent(wx.EVT_TEXT_ENTER.typeId, control.GetId())
    event.SetEventObject(control)
    control.GetEventHandler().ProcessEvent(event)
    harness.pump()


def _spy_on_set_focus(control: Any) -> list[bool]:  # noqa: ANN401
    """Monkeypatch *control*'s ``SetFocus``, recording each call.

    ``SetFocus()`` is a platform/GUI I/O boundary (T-10) -- legitimate
    to spy on directly, since ``FindFocus()``/``HasFocus()`` are
    measured unobservable in this session, even freshly spawned
    (``test_dialog_behavior.py``'s own documented limitation and its
    ``_spy_on_set_focus`` precedent, reproduced here for the same
    reason: this session is never the frontmost, focused app).
    """
    original = control.SetFocus
    calls: list[bool] = []

    def _spy() -> None:
        calls.append(True)
        original()

    control.SetFocus = _spy
    return calls


def _spy_on_destroy(window: Any) -> list[bool]:  # noqa: ANN401
    """Monkeypatch *window*'s ``Destroy``, recording each real call.

    ``Destroy()`` is a platform/GUI I/O boundary (T-10), the same
    category as ``SetFocus()`` above: measured, ``IsBeingDeleted()``
    right after a real ``Destroy()`` -- with no intervening pump --
    still reads ``False`` on this build (a deferred deletion that has
    not yet run), and touching the window again *after* one pump
    raises ``RuntimeError: wrapped C/C++ object ... has been
    deleted``. Spying on the call itself, rather than any later state
    read, is the only way measured to observe "Destroy() really ran"
    without either false negative.
    """
    original = window.Destroy
    calls: list[bool] = []

    def _spy() -> bool:
        calls.append(True)
        return bool(original())

    window.Destroy = _spy
    return calls


def _plate_entry_round_trip() -> dict[str, Any]:
    """Typing a plate then Enter records once, clears, refocuses."""
    calls, restore = _counting_show_notices()
    try:
        frame = app_module.build_main_window(wx.GetApp())
        frame.Show()
        frame.Layout()
        harness.pump()
        try:
            plate_input = harness.find_control(frame, ids.PLATE_INPUT)
            focus_calls = _spy_on_set_focus(plate_input)  # after the bootstrap's own focus_entry()
            plate_input.SetValue("123")
            _post_text_enter(plate_input)
            return {
                "status_text": frame.GetStatusBar().GetStatusText(0),
                "field_value": plate_input.GetValue(),
                "focused": len(focus_calls) > 0,
                "notice_count": len(calls),
            }
        finally:
            _close_without_prompt(frame)
    finally:
        restore()


def _record_btn_click_records_once() -> dict[str, Any]:
    """Clicking Record does exactly what pressing Enter does (A5)."""
    calls, restore = _counting_show_notices()
    try:
        frame = app_module.build_main_window(wx.GetApp())
        frame.Show()
        frame.Layout()
        harness.pump()
        try:
            plate_input = harness.find_control(frame, ids.PLATE_INPUT)
            focus_calls = _spy_on_set_focus(plate_input)  # after the bootstrap's own focus_entry()
            plate_input.SetValue("77")
            harness.click(frame, ids.RECORD_BTN)
            return {
                "status_text": frame.GetStatusBar().GetStatusText(0),
                "field_value": plate_input.GetValue(),
                "focused": len(focus_calls) > 0,
                "notice_count": len(calls),
            }
        finally:
            _close_without_prompt(frame)
    finally:
        restore()


def _console_starts_in_running_state() -> dict[str, Any]:
    """Run the bootstrap and read the console's starting state."""
    frame = app_module.build_main_window(wx.GetApp())
    frame.Show()
    frame.Layout()
    harness.pump()
    try:
        plate_input = harness.find_control(frame, ids.PLATE_INPUT)
        record_btn = harness.find_control(frame, ids.RECORD_BTN)
        status_label = harness.find_control(frame, ids.RIDE_STATUS_LBL)
        return {
            "plate_enabled": plate_input.IsEnabled(),
            "record_enabled": record_btn.IsEnabled(),
            "status_label": status_label.GetLabelText(),
        }
    finally:
        _close_without_prompt(frame)


# --- Phase 8, task 8.5: quit always confirms; macOS X hides --------


def _fire_exit_route(frame: Any) -> None:  # noqa: ANN401
    """Post a real ``EVT_MENU`` for ``wxID_EXIT`` at *frame*.

    Never pumped afterwards: the handler this fires calls
    ``ShowModal()`` synchronously, which is itself the native modal
    loop that runs any ``wx.CallAfter`` a caller already scheduled
    before calling this (the same ``_run_with_action`` technique
    ``test_dialog_behavior.py`` proves works, module docstring).
    """
    real_id = wx.xrc.XRCID("wxID_EXIT")
    event = wx.CommandEvent(wx.EVT_MENU.typeId, real_id)
    event.SetEventObject(frame)
    frame.GetEventHandler().ProcessEvent(event)


def _close_without_prompt(frame: Any) -> None:  # noqa: ANN401
    """Destroy *frame* at scenario cleanup, never through a dialog.

    Measured on windows-latest CI (module docstring): a plain,
    vetoable ``Close()`` at cleanup time -- what
    ``harness.close_window`` always does -- runs the very same
    ``_confirm_quit`` flow File ▸ Exit does on any platform but
    macOS, and nothing in this child dismisses that dialog. Setting
    ``really_quitting`` first makes ``_on_main_frame_close``'s own
    ``not event.CanVeto() or context.app.really_quitting`` guard
    destroy *frame* immediately instead, on every platform -- the
    same guard :func:`_handle_exit_route`'s own forced close already
    relies on. Every scenario that builds ``main_frame`` through
    :func:`~rivercrossing.ui.app.build_main_window` uses this in its
    cleanup ``finally``, never before the behaviour under test runs.
    """
    wx.GetApp().really_quitting = True
    harness.close_window(frame)


def _quit_menu_confirmed_destroys() -> dict[str, Any]:
    """wxID_EXIT + Quit on exit_running_dlg (demo RUNNING)."""
    frame = app_module.build_main_window(wx.GetApp())
    frame.Show()
    frame.Layout()
    harness.pump()
    destroy_calls = _spy_on_destroy(frame)

    def _click_quit() -> None:
        dialog = wx.Window.FindWindowByName(ids.EXIT_RUNNING_DLG)
        harness.click(dialog, pages.WX_ID_OK)

    wx.CallAfter(_click_quit)
    _fire_exit_route(frame)
    return {"frame_being_deleted": len(destroy_calls) > 0}


def _quit_menu_cancelled_stays() -> dict[str, Any]:
    """wxID_EXIT + Cancel on exit_running_dlg: frame survives."""
    frame = app_module.build_main_window(wx.GetApp())
    frame.Show()
    frame.Layout()
    harness.pump()
    try:

        def _click_cancel() -> None:
            dialog = wx.Window.FindWindowByName(ids.EXIT_RUNNING_DLG)
            harness.click(dialog, pages.WX_ID_CANCEL)

        wx.CallAfter(_click_cancel)
        _fire_exit_route(frame)
        return {"frame_being_deleted": frame.IsBeingDeleted(), "frame_shown": frame.IsShown()}
    finally:
        _close_without_prompt(frame)


def _running_ride_shows_exit_running_dlg() -> dict[str, Any]:
    """Fire wxID_EXIT; check exit_running_dlg is what shows."""
    frame = app_module.build_main_window(wx.GetApp())
    frame.Show()
    frame.Layout()
    harness.pump()
    try:
        found: dict[str, bool] = {}

        def _probe_and_cancel() -> None:
            dialog = wx.Window.FindWindowByName(ids.EXIT_RUNNING_DLG)
            found["shown"] = dialog is not None and dialog.IsShown()
            harness.click(dialog, pages.WX_ID_CANCEL)

        wx.CallAfter(_probe_and_cancel)
        _fire_exit_route(frame)
        return {"exit_running_dlg_shown": found.get("shown", False)}
    finally:
        _close_without_prompt(frame)


def _exit_confirm_dlg_shown_when_not_running() -> dict[str, Any]:
    """Show exit_confirm_dlg when the status is not RUNNING."""
    original_ride_status = DemoDataSource.ride_status
    DemoDataSource.ride_status = lambda _self: RideStatus.DRAFT
    try:
        frame = app_module.build_main_window(wx.GetApp())
        frame.Show()
        frame.Layout()
        harness.pump()
        try:
            found: dict[str, bool] = {}

            def _probe_and_cancel() -> None:
                dialog = wx.Window.FindWindowByName(ids.EXIT_CONFIRM_DLG)
                found["shown"] = dialog is not None and dialog.IsShown()
                harness.click(dialog, pages.WX_ID_CANCEL)

            wx.CallAfter(_probe_and_cancel)
            _fire_exit_route(frame)
            return {"exit_confirm_dlg_shown": found.get("shown", False)}
        finally:
            _close_without_prompt(frame)
    finally:
        DemoDataSource.ride_status = original_ride_status


def _finish_first_ends_dialog_stays_running_posts_notice() -> dict[str, Any]:
    """finish_first_btn (A1): ends the dialog, ride stays running."""
    frame = app_module.build_main_window(wx.GetApp())
    frame.Show()
    frame.Layout()
    harness.pump()
    try:

        def _click_finish_first() -> None:
            dialog = wx.Window.FindWindowByName(ids.EXIT_RUNNING_DLG)
            harness.click(dialog, ids.FINISH_FIRST_BTN)

        wx.CallAfter(_click_finish_first)
        _fire_exit_route(frame)
        return {
            "frame_being_deleted": frame.IsBeingDeleted(),
            "status_text": frame.GetStatusBar().GetStatusText(0),
        }
    finally:
        _close_without_prompt(frame)


def _red_x_close_vetoes_and_hides_on_mac() -> dict[str, Any]:
    """Hide main_frame via a plain Close(); never destroy it."""
    frame = app_module.build_main_window(wx.GetApp())
    frame.Show()
    frame.Layout()
    harness.pump()
    frame.Close()
    harness.pump()
    try:
        return {"being_deleted": frame.IsBeingDeleted(), "shown": frame.IsShown()}
    finally:
        _close_without_prompt(frame)


def _mac_reopen_shows_and_raises() -> dict[str, Any]:
    """Show and raise main_frame after the red X hid it."""
    app = wx.GetApp()
    frame = app_module.build_main_window(app)
    frame.Show()
    frame.Layout()
    harness.pump()
    frame.Close()
    harness.pump()
    try:
        app.MacReopenApp()
        harness.pump()
        return {"shown_after_reopen": frame.IsShown()}
    finally:
        _close_without_prompt(frame)


def _query_end_session_cancelled_vetoes() -> dict[str, Any]:
    """Dock Quit, Cancel: the session-end event is vetoed."""
    original_run_dialog = dialogs.run_dialog
    dialogs.run_dialog = lambda _dialog, opener: wx.ID_CANCEL  # noqa: ARG005 -- opener= is a real kwarg
    try:
        app = wx.GetApp()
        frame = app_module.build_main_window(app)
        frame.Show()
        frame.Layout()
        harness.pump()
        try:
            event = wx.CloseEvent(wx.wxEVT_QUERY_END_SESSION)
            event.SetCanVeto(True)  # noqa: FBT003 -- wx API takes a positional bool
            app.ProcessEvent(event)
            return {"vetoed": event.GetVeto()}
        finally:
            _close_without_prompt(frame)
    finally:
        dialogs.run_dialog = original_run_dialog


def _query_end_session_confirmed_does_not_veto() -> dict[str, Any]:
    """Dock Quit, Quit: no veto, and the quit flag is set."""
    original_run_dialog = dialogs.run_dialog
    dialogs.run_dialog = lambda _dialog, opener: wx.ID_OK  # noqa: ARG005 -- opener= is a real kwarg
    try:
        app = wx.GetApp()
        frame = app_module.build_main_window(app)
        frame.Show()
        frame.Layout()
        harness.pump()
        try:
            event = wx.CloseEvent(wx.wxEVT_QUERY_END_SESSION)
            event.SetCanVeto(True)  # noqa: FBT003 -- wx API takes a positional bool
            app.ProcessEvent(event)
            return {"vetoed": event.GetVeto(), "really_quitting": app.really_quitting}
        finally:
            _close_without_prompt(frame)
    finally:
        dialogs.run_dialog = original_run_dialog


def _session_end_confirmed_then_close_destroys_once() -> dict[str, Any]:
    """Skip a follow-on Close() once QUERY_END_SESSION set the flag.

    Closes the gap a QUIT-outcome QUERY_END_SESSION leaves open on its
    own: its own default handler proceeds to call ``TopWindow->
    Close()`` next, a *plain*, vetoable close, not a forced one --
    this is the ``context.app.really_quitting`` half of
    ``_on_main_frame_close``'s ``not event.CanVeto() or ...`` guard,
    never exercised by the QUERY_END_SESSION event alone.
    """
    calls: list[int] = []
    original_run_dialog = dialogs.run_dialog

    def _counting_run_dialog(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001
        calls.append(1)
        return wx.ID_OK

    dialogs.run_dialog = _counting_run_dialog
    try:
        app = wx.GetApp()
        frame = app_module.build_main_window(app)
        frame.Show()
        frame.Layout()
        harness.pump()
        event = wx.CloseEvent(wx.wxEVT_QUERY_END_SESSION)
        event.SetCanVeto(True)  # noqa: FBT003 -- wx API takes a positional bool
        app.ProcessEvent(event)
        destroy_calls = _spy_on_destroy(frame)
        frame.Close()  # plain, not forced -- what QUERY_END_SESSION's own default does next
        return {"being_deleted": len(destroy_calls) > 0, "run_dialog_calls": len(calls)}
    finally:
        dialogs.run_dialog = original_run_dialog


def _forced_close_destroys_without_dialog() -> dict[str, Any]:
    """Close(force=True) destroys the frame; no confirm dialog opens."""
    calls: list[int] = []
    original_run_dialog = dialogs.run_dialog

    def _counting_run_dialog(dialog: Any, opener: Any) -> int:  # noqa: ANN401
        calls.append(1)
        return original_run_dialog(dialog, opener)

    dialogs.run_dialog = _counting_run_dialog
    try:
        frame = app_module.build_main_window(wx.GetApp())
        frame.Show()
        frame.Layout()
        harness.pump()
        destroy_calls = _spy_on_destroy(frame)
        frame.Close(force=True)
        return {"being_deleted": len(destroy_calls) > 0, "run_dialog_calls": len(calls)}
    finally:
        dialogs.run_dialog = original_run_dialog


def _windows_close_cancelled_stays() -> dict[str, Any]:
    """Windows ✕ + Cancel on exit_running_dlg: frame survives.

    Documented Windows ✕ contract (Phase 10, measured on
    windows-latest CI): a plain, vetoable ``frame.Close()`` -- what
    the close box fires -- runs the very same ``_confirm_quit`` flow
    ``_fire_exit_route``'s ``wxID_EXIT`` does (``_on_main_frame_close``
    's non-mac branch), so this mirrors ``_quit_menu_cancelled_stays``
    exactly except for firing a plain ``Close()`` instead of the
    wxID_EXIT menu route.
    """
    frame = app_module.build_main_window(wx.GetApp())
    frame.Show()
    frame.Layout()
    harness.pump()
    try:

        def _click_cancel() -> None:
            dialog = wx.Window.FindWindowByName(ids.EXIT_RUNNING_DLG)
            harness.click(dialog, pages.WX_ID_CANCEL)

        wx.CallAfter(_click_cancel)
        frame.Close()
        return {"frame_being_deleted": frame.IsBeingDeleted(), "frame_shown": frame.IsShown()}
    finally:
        _close_without_prompt(frame)


def _windows_close_confirmed_destroys() -> dict[str, Any]:
    """Windows ✕ + Quit on exit_running_dlg: the frame is destroyed.

    Mirrors ``_quit_menu_confirmed_destroys`` exactly except for
    firing a plain ``frame.Close()`` instead of the wxID_EXIT menu
    route (see :func:`_windows_close_cancelled_stays`'s own
    docstring): both reach ``_on_main_frame_close``'s non-mac branch,
    which destroys *frame* directly on a confirmed ``QUIT`` outcome,
    so no further cleanup close is needed here either.
    """
    frame = app_module.build_main_window(wx.GetApp())
    frame.Show()
    frame.Layout()
    harness.pump()
    destroy_calls = _spy_on_destroy(frame)

    def _click_quit() -> None:
        dialog = wx.Window.FindWindowByName(ids.EXIT_RUNNING_DLG)
        harness.click(dialog, pages.WX_ID_OK)

    wx.CallAfter(_click_quit)
    frame.Close()
    return {"frame_being_deleted": len(destroy_calls) > 0}


# --- Phase 8, task 8.6: live dark mode + menu radio defaults -------


def _fire_menu_event(frame: Any, item_id: str) -> None:  # noqa: ANN401
    """Post a real ``EVT_MENU`` for *item_id* at *frame* and pump it.

    Safe to pump right after, unlike ``_fire_exit_route`` above: no
    theme id ever opens a modal dialog.
    """
    real_id = wx.xrc.XRCID(item_id)
    event = wx.CommandEvent(wx.EVT_MENU.typeId, real_id)
    event.SetEventObject(frame)
    frame.GetEventHandler().ProcessEvent(event)
    harness.pump()


def _theme_radio_checked(frame: Any, item_id: str) -> bool:  # noqa: ANN401
    """Return whether *item_id*'s own menu item is currently checked."""
    item, _menu = frame.GetMenuBar().FindItem(wx.xrc.XRCID(item_id))
    return bool(item.IsChecked())


def _theme_dark_applies_at_runtime() -> dict[str, Any]:
    """mi_theme_dark: SystemAppearance flips dark, radio stays checked.

    Also captures a dark-mode screenshot artifact (Phase 8's own
    visual record) via the same ``harness.screenshot`` machinery
    ``test_screen_smoke.py`` already uses, into the same
    ``_screenshots`` directory.

    Captures the appearance both before and after firing the theme id,
    rather than only the raw post-fire value: macOS's own live-switch
    contract (theme.py's own module docstring) forces ``IsDark()`` to
    the requested value deterministically, but MSW's ``CannotChange``
    contract only documents that it does not change at all -- an
    invariant this caller can check without ever needing to know
    either OS's actual, environment-dependent starting appearance.
    """
    frame = app_module.build_main_window(wx.GetApp())
    frame.Show()
    frame.Layout()
    harness.pump()
    try:
        is_dark_before = wx.SystemSettings.GetAppearance().IsDark()
        _fire_menu_event(frame, ids.MI_THEME_DARK)
        is_dark_after = wx.SystemSettings.GetAppearance().IsDark()
        saved = harness.screenshot(frame, _SCREENSHOT_DIR / "theme_dark.png")
        return {
            "is_dark_after": is_dark_after,
            "appearance_unchanged": is_dark_after == is_dark_before,
            "radio_checked": _theme_radio_checked(frame, ids.MI_THEME_DARK),
            "notice_after": frame.GetStatusBar().GetStatusText(0),
            "screenshot_exists": saved.exists(),
        }
    finally:
        _close_without_prompt(frame)


def _theme_light_round_trip() -> dict[str, Any]:
    """Dark then Light: SystemAppearance and the radio flip back.

    Captures the pre-fire notice text too (see
    :func:`_theme_dark_applies_at_runtime`'s own docstring for why):
    MSW's ``CannotChange`` contract means the notice after this round
    trip is never the generic stub, but *is* the same next-launch text
    both times, so comparing to the untouched pre-fire text is not
    enough on its own to prove a notice posted at all.
    """
    frame = app_module.build_main_window(wx.GetApp())
    frame.Show()
    frame.Layout()
    harness.pump()
    try:
        _fire_menu_event(frame, ids.MI_THEME_DARK)
        _fire_menu_event(frame, ids.MI_THEME_LIGHT)
        return {
            "is_dark_after": wx.SystemSettings.GetAppearance().IsDark(),
            "radio_checked": _theme_radio_checked(frame, ids.MI_THEME_LIGHT),
            "notice_after": frame.GetStatusBar().GetStatusText(0),
        }
    finally:
        _close_without_prompt(frame)


def _theme_system_reapplies_on_sys_colour_changed() -> dict[str, Any]:
    """Dark then System: a guarded re-apply, bounded (best-effort).

    # logic-coverage-exempt: T-10 -- this passthrough spy targets an
    # internal module function (``theme.apply``), not a true I/O
    # boundary, for the same reason ``_counting_show_notices`` above
    # is exempted: the final ``SystemSettings`` state alone cannot
    # distinguish "the guard let exactly one re-apply through" from
    # "it let none, or many, through". Counting real invocations is
    # the only direct way to observe the reentrancy guard; the spy
    # still calls through to the genuine implementation, it never
    # fakes it.
    """
    calls: list[theme.ThemeMode] = []
    original_apply = theme.apply

    def _counting_apply(app: Any, mode: theme.ThemeMode) -> Any:  # noqa: ANN401
        calls.append(mode)
        return original_apply(app, mode)

    theme.apply = _counting_apply
    try:
        frame = app_module.build_main_window(wx.GetApp())
        frame.Show()
        frame.Layout()
        harness.pump()
        try:
            _fire_menu_event(frame, ids.MI_THEME_DARK)
            _fire_menu_event(frame, ids.MI_THEME_SYSTEM)
            return {
                "apply_call_count": len(calls),
                "radio_checked": _theme_radio_checked(frame, ids.MI_THEME_SYSTEM),
            }
        finally:
            _close_without_prompt(frame)
    finally:
        theme.apply = original_apply


def _theme_ids_do_not_post_the_stub_notice_but_zoom_still_does() -> dict[str, Any]:
    """Theme ids post no stub notice (Ok/CannotChange); zoom still does.

    Returns the raw post-fire theme notice text too, not only whether
    it changed: MSW's ``CannotChange`` contract means firing the theme
    id *does* change the status bar on Windows (to the documented
    next-launch text), so "unchanged" alone would read as a false
    positive there for the one fact this scenario actually needs to
    prove on every platform -- that the theme id's own notice, if any,
    is never the generic ``route.label — not yet implemented`` stub
    ``mi_zoom_110`` posts.
    """
    frame = app_module.build_main_window(wx.GetApp())
    frame.Show()
    frame.Layout()
    harness.pump()
    try:
        before = frame.GetStatusBar().GetStatusText(0)
        _fire_menu_event(frame, ids.MI_THEME_DARK)
        after_theme = frame.GetStatusBar().GetStatusText(0)
        _fire_menu_event(frame, "mi_zoom_110")
        after_zoom = frame.GetStatusBar().GetStatusText(0)
        return {
            "theme_notice_unchanged": after_theme == before,
            "theme_notice_after": after_theme,
            "zoom_stub_notice": after_zoom,
        }
    finally:
        _close_without_prompt(frame)


_SCENARIOS: dict[str, Callable[[], dict[str, Any]]] = {
    "sash_round_trip": _sash_round_trip,
    "hide_times_columns_round_trip": _hide_times_columns_round_trip,
    "hide_times_leaves_clock_shown": _hide_times_leaves_clock_shown,
    "state_enablement_round_trip": _state_enablement_round_trip,
    "plate_entry_round_trip": _plate_entry_round_trip,
    "record_btn_click_records_once": _record_btn_click_records_once,
    "console_starts_in_running_state": _console_starts_in_running_state,
    "quit_menu_confirmed_destroys": _quit_menu_confirmed_destroys,
    "quit_menu_cancelled_stays": _quit_menu_cancelled_stays,
    "running_ride_shows_exit_running_dlg": _running_ride_shows_exit_running_dlg,
    "exit_confirm_dlg_shown_when_not_running": _exit_confirm_dlg_shown_when_not_running,
    "finish_first_ends_dialog_stays_running_posts_notice": (
        _finish_first_ends_dialog_stays_running_posts_notice
    ),
    "red_x_close_vetoes_and_hides_on_mac": _red_x_close_vetoes_and_hides_on_mac,
    "mac_reopen_shows_and_raises": _mac_reopen_shows_and_raises,
    "query_end_session_cancelled_vetoes": _query_end_session_cancelled_vetoes,
    "query_end_session_confirmed_does_not_veto": _query_end_session_confirmed_does_not_veto,
    "session_end_confirmed_then_close_destroys_once": (
        _session_end_confirmed_then_close_destroys_once
    ),
    "forced_close_destroys_without_dialog": _forced_close_destroys_without_dialog,
    "windows_close_cancelled_stays": _windows_close_cancelled_stays,
    "windows_close_confirmed_destroys": _windows_close_confirmed_destroys,
    "theme_dark_applies_at_runtime": _theme_dark_applies_at_runtime,
    "theme_light_round_trip": _theme_light_round_trip,
    "theme_system_reapplies_on_sys_colour_changed": (
        _theme_system_reapplies_on_sys_colour_changed
    ),
    "theme_ids_do_not_post_the_stub_notice_but_zoom_still_does": (
        _theme_ids_do_not_post_the_stub_notice_but_zoom_still_does
    ),
}


def _run(scenario_name: str) -> dict[str, Any]:
    """Run *scenario_name*; turn any exception into an error result."""
    scenario = _SCENARIOS.get(scenario_name)
    if scenario is None:
        return {"ok": False, "error": f"unknown scenario {scenario_name!r}", "data": None}
    try:
        data = scenario()
    except Exception as exc:  # noqa: BLE001 -- reported in the envelope, not swallowed
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "data": None}
    return {"ok": True, "error": None, "data": data}


_EXPECTED_ARGC = 2  # argv[0] is the script path, argv[1] the scenario name


def main(argv: list[str]) -> int:
    """Run the scenario named in *argv*; print one JSON line to stdout.

    The JSON line on stdout *is* this program's whole contract with
    its parent (module docstring) -- printing it is not debug output
    left behind, it is the point. ``faulthandler.enable()`` runs
    first, before anything else here (module docstring): a native
    crash later in this process still writes a Python traceback to
    stderr instead of leaving the parent with only a bare non-zero
    exit code.
    """
    faulthandler.enable()
    # Hard bound on this child: a hung scenario must die with its
    # thread stacks on stderr before the parent's
    # SCENARIO_TIMEOUT_SECONDS (30 s) -- measured on windows-latest CI
    # (PR #9): one hung scenario stalled the whole functional pass for
    # > 900 s. dump_traceback_later is best-effort (it needs the GIL);
    # the os._exit timer is the hard bound and does not. 124 mirrors
    # the rerun wrapper's own timed-out exit code.
    faulthandler.dump_traceback_later(scenario_runner.SCENARIO_CHILD_BOUND_SECONDS, exit=False)
    threading.Timer(
        scenario_runner.SCENARIO_CHILD_BOUND_SECONDS + 2, os._exit, args=(124,)
    ).start()
    if len(argv) != _EXPECTED_ARGC:
        print(  # noqa: T201 -- the child's entire contract with its parent
            json.dumps({"ok": False, "error": "usage: <script> <scenario>", "data": None}),
            flush=True,
        )
        return 2
    # Bound to a name for the rest of main(): an unbound App is
    # collected immediately (measured), and wx.GetApp() then reports
    # PyNoAppError for everything the scenario tries after it.
    # app_module.build_app(), not a plain wx.App(): the quit-flow
    # scenarios below need the real MacReopenApp override and
    # really_quitting flag it carries.
    app = app_module.build_app()  # noqa: F841 -- kept alive by this binding, never read
    wx.Log.SetActiveTarget(wx.LogStderr())
    result = _run(argv[1])
    # flush=True: measured on windows-latest CI (module docstring), a
    # child killed by the parent's timeout can otherwise never flush
    # this, its one and only line of output, out of the pipe at all.
    print(json.dumps(result), flush=True)  # noqa: T201 -- the child's entire contract
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
