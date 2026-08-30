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
import sqlite3
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import harness
import pages
import scenario_runner
import wx
import wx.dataview
import wx.xrc

from rivercrossing.cards import Shoe
from rivercrossing.demo import DemoDataSource
from rivercrossing.ride import Event, RideConfig, RideEngine, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.store import Store
from rivercrossing.store import backup as backup_module
from rivercrossing.ui import app as app_module
from rivercrossing.ui import feed_model, ids, sound, theme
from rivercrossing.ui.accelerators import ACCELERATOR_TABLE, Accelerator
from rivercrossing.ui.presenters.console import ConsolePresenter
from rivercrossing.ui.presenters.data_source import EngineDataSource, format_duration
from rivercrossing.ui.presenters.settings import (
    ZOOM_LADDER,
    AppSettings,
    load_settings,
    save_settings,
)
from rivercrossing.ui.views import MainFrame, dialogs, rider_editor
from rivercrossing.ui.views.main_frame import REOPENED_INFOBAR
from rivercrossing.ui.views.ride_library import COL_NAME, COL_STATUS
from rivercrossing.ui.views.shortcuts import ShortcutsDialog

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["main"]

_SCREENSHOT_DIR = Path(__file__).resolve().parent / "_screenshots"

# E8.1.1 hermeticity: every scenario builds the app through
# _build_app_window, which injects a per-process tmp settings file.
# Each scenario runs in its OWN spawned interpreter, so a module-level
# mkdtemp path is per-scenario, and no scenario ever reads or writes
# the guest's real user config dir (measured: the theme-dark scenario's
# own persisted appearance was leaking into later launches in the same
# VM clone, flipping appearance_unchanged on rerun). Scenarios that
# need a specific file pass settings_path= explicitly and the helper
# leaves it untouched.
_SCENARIO_SETTINGS_PATH = Path(tempfile.mkdtemp(prefix="rc-scenario-settings-")) / "settings.json"


def _build_app_window(**kwargs: Any) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """build_main_window with a per-scenario settings path (E8.1.1)."""
    kwargs.setdefault("settings_path", _SCENARIO_SETTINGS_PATH)
    return app_module.build_main_window(wx.GetApp(), **kwargs)


def _visible_column_titles(crossings_list: Any) -> list[str]:  # noqa: ANN401
    """Return the titles of every non-hidden column, in column order."""
    return [
        crossings_list.GetColumn(index).GetTitle()
        for index in range(crossings_list.GetColumnCount())
        if not crossings_list.GetColumn(index).IsHidden()
    ]


_SASH_ROUND_TRIP_ATTEMPTS = 5


def _sash_round_trip_once(resource: Any, settings_path: Path) -> dict[str, Any]:  # noqa: ANN401
    """One attempt at the sash round-trip; may raise ``LookupError``."""

    def _save(sash: int | None, geometry: tuple[int, int, int, int] | None) -> None:
        save_settings(
            AppSettings(
                appearance="system",
                sound_on=True,
                hide_times=False,
                zoom_percent=100,
                splitter_sash=sash,
                window_geometry=geometry,
            ),
            settings_path,
        )

    first_window = harness.load_window(resource, ids.MAIN_FRAME, frame=True)
    first_window.Show()
    first_window.Layout()
    harness.pump()
    first_console = MainFrame(
        first_window,
        data_source=DemoDataSource(),
        on_layout_changed=_save,
    )
    first_console.main_splitter.SetSashPosition(300)
    first_console.persist_layout()
    harness.close_window(first_window)

    saved = load_settings(settings_path)
    second_window = harness.load_window(resource, ids.MAIN_FRAME, frame=True)
    second_window.Show()
    second_window.Layout()
    harness.pump()
    try:
        second_console = MainFrame(
            second_window,
            data_source=DemoDataSource(),
            initial_sash=saved.splitter_sash,
        )
        restored = second_console.main_splitter.GetSashPosition()
    finally:
        harness.close_window(second_window)

    return {"restored_sash": restored}


def _sash_round_trip() -> dict[str, Any]:
    """Persist a sash position to disk, rebuild fresh, read it back.

    E8.1.1 replaced the process-lifetime sash global with the disk-
    backed settings store, so this writes to a temp settings file
    (never the real user config dir) and the rebuild restores from it.

    Retries the whole sequence: the first attempt that raises no
    ``LookupError`` wins.
    """
    resource = harness.load_xrc_resources()
    with tempfile.TemporaryDirectory(prefix="rc-sash-") as tmp:
        settings_path = Path(tmp) / "settings.json"
        last_error: LookupError | None = None
        for _attempt in range(_SASH_ROUND_TRIP_ATTEMPTS):
            try:
                return _sash_round_trip_once(resource, settings_path)
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
    """Typing a plate with no ride open rejects it (R-31, E5.4.2).

    The bootstrap roster is empty (no store-backed ride is open), so
    every plate is unknown: the entry is refused with the ERROR notice,
    the field is kept, focus returns, and no crossing is recorded --
    the console's correct empty state until a store-backed ride is
    opened.
    """
    frame = _build_app_window()
    frame.Show()
    frame.Layout()
    harness.pump()
    try:
        plate_input = harness.find_control(frame, ids.PLATE_INPUT)
        focus_calls = _spy_on_set_focus(plate_input)  # after the bootstrap's own focus_entry()
        plate_input.SetValue("123")
        _post_text_enter(plate_input)
        model = harness.find_control(frame, ids.CROSSINGS_LIST).GetModel()
        return {
            "feed_plates": [
                model.GetValueByRow(row, feed_model.COL_PLATE) for row in range(model.GetCount())
            ],
            "field_value": plate_input.GetValue(),
            "focused": len(focus_calls) > 0,
            "crossings_label": harness.find_control(frame, ids.CROSSINGS_COUNT_LBL).GetLabelText(),
            "status_text": frame.GetStatusBar().GetStatusText(0),
        }
    finally:
        _close_without_prompt(frame)


def _record_btn_click_records_once() -> dict[str, Any]:
    """Clicking Record with no ride open rejects it (R-31, E5.4.2)."""
    frame = _build_app_window()
    frame.Show()
    frame.Layout()
    harness.pump()
    try:
        plate_input = harness.find_control(frame, ids.PLATE_INPUT)
        focus_calls = _spy_on_set_focus(plate_input)  # after the bootstrap's own focus_entry()
        plate_input.SetValue("77")
        harness.click(frame, ids.RECORD_BTN)
        model = harness.find_control(frame, ids.CROSSINGS_LIST).GetModel()
        return {
            "feed_plates": [
                model.GetValueByRow(row, feed_model.COL_PLATE) for row in range(model.GetCount())
            ],
            "field_value": plate_input.GetValue(),
            "focused": len(focus_calls) > 0,
            "crossings_label": harness.find_control(frame, ids.CROSSINGS_COUNT_LBL).GetLabelText(),
            "status_text": frame.GetStatusBar().GetStatusText(0),
        }
    finally:
        _close_without_prompt(frame)


def _console_starts_in_running_state() -> dict[str, Any]:
    """Run the bootstrap and read the console's starting state."""
    frame = _build_app_window()
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
    frame = _build_app_window()
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
    frame = _build_app_window()
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
    frame = _build_app_window()
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
    """Show exit_confirm_dlg when the console ride is not RUNNING.

    E5.4.2: the quit flow reads the live presenter engine's state, so
    the DRAFT case is set up by keeping the bootstrap engine DRAFT
    (``RideEngine.start`` patched to a no-op before
    ``build_main_window`` auto-starts it). The engine itself is real;
    only the auto-start is suppressed, in this one spawned interpreter.
    """
    original_start = RideEngine.start
    RideEngine.start = lambda _self, _at=None: None  # type: ignore[assignment]
    try:
        frame = _build_app_window()
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
        RideEngine.start = original_start


def _red_x_close_vetoes_and_hides_on_mac() -> dict[str, Any]:
    """Hide main_frame via a plain Close(); never destroy it."""
    frame = _build_app_window()
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
    frame = _build_app_window()
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
        frame = _build_app_window()
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
        frame = _build_app_window()
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
        frame = _build_app_window()
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
        frame = _build_app_window()
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
    frame = _build_app_window()
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
    which on a confirmed ``QUIT`` outcome defers the destroy through
    ``wx.CallAfter`` -- a synchronous ``Destroy()`` inside
    ``EVT_CLOSE`` right after the confirm modal unwinds deadlocks
    wxMSW, the stage-3 failure this scenario exists to verify (only
    runnable on windows-latest CI). The pump after ``Close()`` runs
    that deferred destroy before the JSON envelope is printed, so no
    further cleanup close is needed here either.
    """
    frame = _build_app_window()
    frame.Show()
    frame.Layout()
    harness.pump()
    destroy_calls = _spy_on_destroy(frame)

    def _click_quit() -> None:
        dialog = wx.Window.FindWindowByName(ids.EXIT_RUNNING_DLG)
        harness.click(dialog, pages.WX_ID_OK)

    wx.CallAfter(_click_quit)
    frame.Close()
    harness.pump()  # run the deferred destroy the MSW fix schedules
    return {"frame_being_deleted": len(destroy_calls) > 0}


# --- E5.2.3: the exit-with-running-ride dialog (R-51) -------------


def _exit_running_dlg_probe_and_cancel() -> dict[str, Any]:
    """exit_running_dlg shows 3 buttons, message_lbl, Cancel default."""
    frame = _build_app_window()
    frame.Show()
    frame.Layout()
    harness.pump()
    try:
        found: dict[str, Any] = {}

        def _probe_and_cancel() -> None:
            dialog = wx.Window.FindWindowByName(ids.EXIT_RUNNING_DLG)
            found["shown"] = dialog is not None and dialog.IsShown()
            if dialog is None:
                return
            found["message_lbl"] = harness.find_control(dialog, ids.MESSAGE_LBL).GetLabelText()
            for name in (pages.WX_ID_CANCEL, ids.FINISH_FIRST_BTN, pages.WX_ID_OK):
                found[f"has_{name}"] = wx.Window.FindWindowByName(name, dialog) is not None
            default_item = dialog.GetDefaultItem()
            found["default_name"] = default_item.GetName() if default_item is not None else None
            harness.click(dialog, pages.WX_ID_CANCEL)

        wx.CallAfter(_probe_and_cancel)
        _fire_exit_route(frame)
        return {
            "exit_running_dlg_shown": found.get("shown", False),
            "message_lbl": found.get("message_lbl", ""),
            "has_cancel": found.get("has_wxID_CANCEL", False),
            "has_finish_first": found.get("has_finish_first_btn", False),
            "has_quit": found.get("has_wxID_OK", False),
            "default_name": found.get("default_name"),
            "frame_being_deleted": frame.IsBeingDeleted(),
            "frame_shown": frame.IsShown(),
        }
    finally:
        _close_without_prompt(frame)


def _finish_first_routes_to_the_finish_flow() -> dict[str, Any]:
    """finish_first_btn ends the exit dialog, then runs the finish flow.

    ``_handle_finish_route`` opens finish_confirm_dlg modally inside
    the exit flow's own synchronous unwind, so the scenario cannot
    click it from its own code -- the same wall the
    ``query_end_session_*`` scenarios hit. ``dialogs.run_dialog`` is
    intercepted (the suite's established pattern) to answer a
    confirmed OK for the finish dialog only; the exit dialog itself
    still shows as a real modal and is dismissed by clicking
    ``finish_first_btn``. That proves the routing: FINISH_FIRST hands
    off to the E4.4.4 finish path, whose confirmed finish runs
    ``presenter.on_finish`` and leaves the app up.
    """
    original_run_dialog = dialogs.run_dialog
    found: dict[str, Any] = {}

    def _auto_ok_finish(dialog: Any, opener: Any) -> int:  # noqa: ANN401
        if dialog.GetName() == ids.FINISH_CONFIRM_DLG:
            found["finish_confirm_shown"] = True
            return wx.ID_OK
        return original_run_dialog(dialog, opener)

    dialogs.run_dialog = _auto_ok_finish
    try:
        frame = _build_app_window()
        frame.Show()
        frame.Layout()
        harness.pump()

        def _click_finish_first() -> None:
            dialog = wx.Window.FindWindowByName(ids.EXIT_RUNNING_DLG)
            harness.click(dialog, ids.FINISH_FIRST_BTN)

        wx.CallAfter(_click_finish_first)
        _fire_exit_route(frame)
        return {
            "finish_confirm_shown": found.get("finish_confirm_shown", False),
            "status_text": frame.GetStatusBar().GetStatusText(0),
            "frame_being_deleted": frame.IsBeingDeleted(),
        }
    finally:
        dialogs.run_dialog = original_run_dialog
        _close_without_prompt(frame)


def _quit_keep_running_writes_closed_at_and_stays_running() -> dict[str, Any]:
    """Quit-keep-running: closed_at written, ride untouched, app quits.

    The app is built over a real Store whose open recorded the running
    ride's id (E5.2.1): confirming Quit on the exit dialog must stamp
    that session's closed_at, leave the ride row's status alone, and
    destroy the frame -- the bookkeeping the resume dialog (E5.2.2)
    will read next launch.
    """
    db_path = Path(tempfile.mkdtemp(prefix="rc-exit-")) / "rides.db"
    boot = Store.open(db_path)
    try:
        config = RideConfig(
            name="GORBA EPIC 2026",
            event_date=date(2026, 9, 20),
            venue="Sea to Sky Gondola",
            lap_km=8.0,
            organizer="GORBA",
            scorer="K. Singh",
            planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive local, Store's own contract
            planned_duration_s=21600,
            min_lap_s=1080,
            entry_mode=EntryMode.MIXED,
            plate_model=PlateModel.RIDER_POOLED,
        )
        ride_id = boot.create_ride(config)
    finally:
        boot.close()

    store = Store.open(db_path, active_ride_id=ride_id)
    frame = _build_app_window(store=store)
    frame.Show()
    frame.Layout()
    harness.pump()
    destroy_calls = _spy_on_destroy(frame)

    def _click_quit() -> None:
        dialog = wx.Window.FindWindowByName(ids.EXIT_RUNNING_DLG)
        harness.click(dialog, pages.WX_ID_OK)

    wx.CallAfter(_click_quit)
    _fire_exit_route(frame)
    harness.pump()
    store.close()

    reopened = Store.open(db_path)
    try:
        state = reopened.session_state()
        status = reopened._conn.execute(
            "SELECT status FROM ride WHERE id = ?", (ride_id,)
        ).fetchone()[0]
    finally:
        reopened.close()
    return {
        "frame_being_deleted": len(destroy_calls) > 0,
        "session_state": state.value,
        "ride_status": status,
    }


# --- E5.2.2: the resume dialog + reopened banner (R-52) -------------


def _resume_db_path(prefix: str) -> Path:
    """Return a fresh db file path under a temp dir named *prefix*."""
    return Path(tempfile.mkdtemp(prefix=prefix)) / "rides.db"


def _resume_ride_config() -> RideConfig:
    """Return the store ride config the resume scenarios use."""
    return RideConfig(
        name="GORBA EPIC 2026",
        event_date=date(2026, 9, 20),
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive local, Store's own contract
        planned_duration_s=21600,
        min_lap_s=1080,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )


@dataclass(frozen=True, slots=True)
class _ResumeRideSpec:
    """What a resume scenario's store ride must look like.

    ``quit_cleanly`` chooses the previous session's bookkeeping (a
    clean quit-keep-running, or a crash). ``ended_at`` pins the copy's
    time in that session row -- ``closed_at`` for a quit,
    ``heartbeat_at`` for a crash. ``start_at`` appends the ride's
    start event (the elapsed proof), and ``finish_and_reopen`` appends
    finish + reopen so the replay lands in REOPENED.
    """

    quit_cleanly: bool
    ended_at: datetime | None = None
    start_at: datetime | None = None
    finish_and_reopen: bool = False


def _append_ride_events(store: Store, ride_id: int, spec: _ResumeRideSpec) -> None:
    """Append start (and finish+reopen) events for the replay state.

    The events are produced by a real engine over an empty roster with
    the ride's own shape, exactly as ``store.roster_for`` will rebuild
    it at launch -- the store persists only the events, and
    ``load_engine`` reproduces the state by replaying them (E5.1.2).
    """
    if spec.start_at is None:
        return
    config = _resume_ride_config()
    roster = Roster(
        entry_mode=config.entry_mode,
        plate_model=config.plate_model,
        max_team_size=config.max_team_size,
    )
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    engine = RideEngine(config=config, shoe=shoe, clock=lambda: spec.start_at, roster=roster)
    store.append(ride_id, engine.start(at=spec.start_at))
    if spec.finish_and_reopen:
        store.append(ride_id, engine.finish())
        store.append(ride_id, engine.reopen())


def _create_resumed_ride(db_path: Path, spec: _ResumeRideSpec) -> int:
    """Create a store ride and a previous session that warrants resume.

    The previous session records the ride as running at exit: closed
    cleanly (``spec.quit_cleanly`` -- a quit-keep-running) or left
    open (a crash). ``spec.ended_at`` pins the copy's time in the
    session row -- ``closed_at`` for a quit, ``heartbeat_at`` for a
    crash -- so the scenario can assert the exact HH:MM in the wording.
    """
    boot = Store.open(db_path)
    try:
        ride_id = boot.create_ride(_resume_ride_config())
        _append_ride_events(boot, ride_id, spec)
    finally:
        boot.close()

    session = Store.open(db_path, active_ride_id=ride_id)
    if spec.quit_cleanly:
        session.close_session()
    session.close()

    if spec.ended_at is not None:
        column = "closed_at" if spec.quit_cleanly else "heartbeat_at"
        with sqlite3.connect(str(db_path)) as conn:  # commits on exit
            conn.execute(
                f"UPDATE app_session SET {column} = ?"  # noqa: S608 -- column is a fixed literal, never input
                " WHERE id = (SELECT id FROM app_session ORDER BY id DESC LIMIT 1)",
                (int(spec.ended_at.timestamp()),),
            )
    return ride_id


def _resume_dlg_quit_wording_shows() -> dict[str, Any]:
    """RUNNING_AT_EXIT launch: resume_dlg shows the quit wording."""
    db_path = _resume_db_path("rc-resume-quit-")
    _create_resumed_ride(
        db_path,
        _ResumeRideSpec(
            quit_cleanly=True,
            ended_at=datetime(2026, 9, 20, 12, 41),  # noqa: DTZ001 -- local, pinned for the copy
        ),
    )
    store = Store.open(db_path)
    found: dict[str, Any] = {}

    def _probe_and_continue() -> None:
        dialog = wx.Window.FindWindowByName(ids.RESUME_DLG)
        found["shown"] = dialog is not None and dialog.IsShown()
        if dialog is None:
            return
        found["message_lbl"] = harness.find_control(dialog, ids.MESSAGE_LBL).GetLabelText()
        default_item = dialog.GetDefaultItem()
        found["continue_is_default"] = default_item.GetName() if default_item is not None else None
        harness.click(dialog, ids.CONTINUE_BTN)

    wx.CallAfter(_probe_and_continue)
    frame = _build_app_window(store=store)
    frame.Show()
    frame.Layout()
    harness.pump()
    try:
        return {
            "resume_dlg_shown": found.get("shown", False),
            "message_lbl": found.get("message_lbl", ""),
            "continue_is_default": found.get("continue_is_default"),
        }
    finally:
        store.close()
        _close_without_prompt(frame)


def _resume_dlg_crash_wording_shows() -> dict[str, Any]:
    """CRASHED-with-ride launch: resume_dlg shows the crash wording."""
    db_path = _resume_db_path("rc-resume-crash-")
    _create_resumed_ride(
        db_path,
        _ResumeRideSpec(
            quit_cleanly=False,
            ended_at=datetime(2026, 9, 20, 12, 37),  # noqa: DTZ001 -- local, pinned for the copy
        ),
    )
    store = Store.open(db_path)
    found: dict[str, Any] = {}

    def _probe_and_continue() -> None:
        dialog = wx.Window.FindWindowByName(ids.RESUME_DLG)
        found["shown"] = dialog is not None and dialog.IsShown()
        if dialog is None:
            return
        found["message_lbl"] = harness.find_control(dialog, ids.MESSAGE_LBL).GetLabelText()
        harness.click(dialog, ids.CONTINUE_BTN)

    wx.CallAfter(_probe_and_continue)
    frame = _build_app_window(store=store)
    frame.Show()
    frame.Layout()
    harness.pump()
    try:
        return {
            "resume_dlg_shown": found.get("shown", False),
            "message_lbl": found.get("message_lbl", ""),
        }
    finally:
        store.close()
        _close_without_prompt(frame)


def _resume_continue_loads_ride_with_elapsed() -> dict[str, Any]:
    """Continue resumes the store ride; the clock shows true elapsed.

    The ride started at 10:00 (the store's replayed actual_start) and
    the launch injects a fixed 11:00 clock, so the resumed console's
    elapsed must read 1:00:00 -- the wall clock kept counting while
    the app was closed (R-30), never the demo engine's ~0:00:00.

    The clock label refreshes on the presenter's 1 s tick timer, and a
    wx.Timer never fires under a bare SafeYield without a live loop
    (measured; the flush_deferred_deletions precedent), so the read
    runs on a real ``MainLoop``: the Continue click is a CallAfter
    into the resume modal, the label is read at 1.5 s (after the tick)
    and the frame force-closed so ``MainLoop`` returns.
    """
    db_path = _resume_db_path("rc-resume-elapsed-")
    start = datetime(2026, 9, 20, 10, 0)  # noqa: DTZ001 -- local, the ride's actual_start
    _create_resumed_ride(db_path, _ResumeRideSpec(quit_cleanly=True, start_at=start))
    store = Store.open(db_path)
    clock = _ScenarioClock(datetime(2026, 9, 20, 11, 0))  # noqa: DTZ001 -- fixed fake launch clock
    captured: dict[str, Any] = {}

    def _click_continue() -> None:
        dialog = wx.Window.FindWindowByName(ids.RESUME_DLG)
        if dialog is not None:
            harness.click(dialog, ids.CONTINUE_BTN)

    def _read_clock_then_close() -> None:
        captured["clock_elapsed"] = harness.find_control(
            frame, ids.CLOCK_ELAPSED_LBL
        ).GetLabelText()
        captured["status_label"] = harness.find_control(frame, ids.RIDE_STATUS_LBL).GetLabelText()
        _close_without_prompt(frame)

    wx.CallAfter(_click_continue)
    frame = _build_app_window(store=store, clock=clock)
    frame.Show()
    frame.Layout()
    wx.CallLater(1500, _read_clock_then_close)
    wx.GetApp().MainLoop()
    store.close()
    expected = format_duration((clock() - start).total_seconds())  # type: ignore[operator]
    return {
        "clock_elapsed": captured.get("clock_elapsed", ""),
        "status_label": captured.get("status_label", ""),
        "expected_elapsed": expected,
    }


def _resume_library_opens_ride_library() -> dict[str, Any]:
    """Open library on resume_dlg: ride_library_dlg opens instead.

    The library open is deferred through wx.CallAfter by the resume
    flow (app.py), so it fires on a real ``MainLoop`` -- the same
    reason the elapsed scenario runs one. The resume dialog is clicked
    via CallAfter into its own modal; the library opens on the loop; a
    second CallAfter (FIFO after the deferred open) probes and
    dismisses it inside the library's own modal; a 1.5 s CallLater
    then closes the frame so ``MainLoop`` has no window to keep -- the
    exact pattern the elapsed scenario uses successfully.
    """
    db_path = _resume_db_path("rc-resume-library-")
    _create_resumed_ride(db_path, _ResumeRideSpec(quit_cleanly=True))
    store = Store.open(db_path)
    found: dict[str, Any] = {}

    def _click_library() -> None:
        dialog = wx.Window.FindWindowByName(ids.RESUME_DLG)
        found["resume_shown"] = dialog is not None and dialog.IsShown()
        if dialog is not None:
            harness.click(dialog, ids.LIBRARY_BTN)

    def _probe_and_dismiss_library() -> None:
        library = wx.Window.FindWindowByName(ids.RIDE_LIBRARY_DLG)
        found["library_shown"] = library is not None and library.IsShown()
        if library is not None:
            # EndModal directly, never harness.click here: click pumps
            # (SafeYield) inside the library's own modal loop, which
            # re-enters it and hangs (measured 2026-08-29;
            # tools/resume_scenario_repro.py reproduces it -- this was
            # the "resume_library" flake's hang).
            wx.CallAfter(library.EndModal, wx.ID_CLOSE)

    wx.CallAfter(_click_library)
    frame = _build_app_window(store=store)
    frame.Show()
    frame.Layout()
    wx.CallAfter(_probe_and_dismiss_library)
    wx.CallLater(1500, lambda: _close_without_prompt(frame))
    wx.GetApp().MainLoop()
    try:
        return {
            "resume_dlg_shown": found.get("resume_shown", False),
            "library_shown": found.get("library_shown", False),
        }
    finally:
        store.close()


def _resume_reopened_ride_shows_reopened_infobar() -> dict[str, Any]:
    """Continue a REOPENED ride: the corrections banner shows by name.

    REOPENED is a corrections-only state (spec §3, R-36); resuming it
    must show the code-constructed, SetName()-named banner (E5.2.2).
    """
    db_path = _resume_db_path("rc-resume-reopen-")
    _create_resumed_ride(
        db_path,
        _ResumeRideSpec(
            quit_cleanly=True,
            start_at=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- local
            finish_and_reopen=True,
        ),
    )
    store = Store.open(db_path)

    def _click_continue() -> None:
        dialog = wx.Window.FindWindowByName(ids.RESUME_DLG)
        if dialog is not None:
            harness.click(dialog, ids.CONTINUE_BTN)

    wx.CallAfter(_click_continue)
    frame = _build_app_window(store=store)
    frame.Show()
    frame.Layout()
    harness.pump()
    try:
        bar = wx.Window.FindWindowByName(REOPENED_INFOBAR, frame)
        return {
            "infobar_resolves": bar is not None,
            "infobar_shown": bool(bar is not None and bar.IsShown()),
            "status_label": harness.find_control(frame, ids.RIDE_STATUS_LBL).GetLabelText(),
        }
    finally:
        store.close()
        _close_without_prompt(frame)


def _confirm_delete_on_dialog(dialog: Any, ride_name: str) -> dict[str, Any]:  # noqa: ANN401
    """Type *ride_name* into the dialog and click Delete; report facts.

    Drives ``delete_ride_dlg`` with the harness's direct-injection
    pattern (SetValue fires EVT_TEXT; a posted CommandEvent fires
    EVT_BUTTON): records the interpolated ``message_lbl`` and the gate
    state after typing, then clicks Delete to end the modal. Returns
    the observations plus whether the modal confirmed as Delete.
    """
    found: dict[str, Any] = {}

    def _safe_end_modal() -> None:
        if dialog.IsModal() and dialog.GetReturnCode() == 0:
            dialog.EndModal(wx.ID_CANCEL)

    def _type_and_confirm() -> None:
        found["message_lbl"] = harness.find_control(dialog, ids.MESSAGE_LBL).GetLabelText()
        harness.type_text(dialog, ids.CONFIRM_NAME_INPUT, ride_name)
        found["delete_enabled_on_exact"] = harness.find_control(
            dialog, pages.WX_ID_DELETE
        ).IsEnabled()
        harness.click(dialog, pages.WX_ID_DELETE)

    wx.CallAfter(_type_and_confirm)
    wx.CallAfter(_safe_end_modal)
    result = dialog.ShowModal()
    found["confirmed"] = result == wx.ID_DELETE
    return found


def _delete_ride_dlg_backup_written_before_delete() -> dict[str, Any]:
    """Confirm on delete_ride_dlg writes a backup, then deletes.

    The E5.3.2 functional proof of R-18's "automatic database backup
    is written first": drives the real ``delete_ride_dlg`` (name
    interpolated into ``message_lbl``, type-to-confirm gate armed),
    types the exact name, clicks Delete, and -- the callback E5.4 will
    thread from the library -- runs ``Store.delete_ride``. The facts
    returned are all first-class disk/store observations: whether a
    backup existed *before* the delete ran, whether one exists after,
    whether that backup reopens with the ride and a clean integrity
    check, and whether the ride row is gone.
    """
    db_path = Path(tempfile.mkdtemp(prefix="rc-delete-")) / "rides.db"
    boot = Store.open(db_path)
    try:
        config = RideConfig(
            name="Club poker night",
            event_date=date(2026, 9, 20),
            venue="Sea to Sky Gondola",
            lap_km=8.0,
            organizer="GORBA",
            scorer="K. Singh",
            planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive local, Store's own contract
            planned_duration_s=21600,
            min_lap_s=1080,
            entry_mode=EntryMode.MIXED,
            plate_model=PlateModel.RIDER_POOLED,
        )
        ride_id = boot.create_ride(config)
        ride_name = "Club poker night"
    finally:
        boot.close()

    store = Store.open(db_path)
    dialog = harness.load_xrc_resources().LoadDialog(None, ids.DELETE_RIDE_DLG)
    try:
        message_lbl = wx.Window.FindWindowByName(ids.MESSAGE_LBL, dialog)
        if message_lbl is not None:
            message_lbl.SetLabel(dialogs.delete_ride_message(ride_name))
        dialogs.bind_delete_confirmation_gate(dialog, ride_name)
        delete_btn = wx.Window.FindWindowByName(pages.WX_ID_DELETE, dialog)
        if delete_btn is not None:
            delete_btn.Bind(wx.EVT_BUTTON, lambda event: dialog.EndModal(event.GetId()))
        found = _confirm_delete_on_dialog(dialog, ride_name)
        backup_dir = backup_module.backup_dir_for(db_path)
        found["backup_exists_before_delete"] = backup_dir.is_dir() and bool(
            list(backup_dir.glob("*.db"))
        )
        if found["confirmed"]:
            # The exact confirmed-delete callback E5.4 wires from the
            # library: Store.delete_ride writes its backup first.
            store.delete_ride(ride_id, ride_name)
        found["backup_exists"] = backup_dir.is_dir() and bool(list(backup_dir.glob("*.db")))
        found["ride_removed"] = store.rides() == []
        if found["backup_exists"]:
            backup_file = max(backup_dir.glob("*.db"))
            reopened = Store.open(backup_file)
            try:
                found["backup_reopens"] = [ride.name for ride in reopened.rides()] == [ride_name]
                with sqlite3.connect(str(backup_file)) as conn:
                    found["backup_integrity"] = (
                        conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                    )
            finally:
                reopened.close()
        else:
            found["backup_reopens"] = False
            found["backup_integrity"] = False
    finally:
        store.close()
        if dialog is not None and not dialog.IsBeingDeleted():
            dialog.Destroy()

    return {
        "message_lbl": found.get("message_lbl", ""),
        "delete_enabled_on_exact": found.get("delete_enabled_on_exact", False),
        "backup_exists_before_delete": found.get("backup_exists_before_delete", False),
        "backup_exists": found.get("backup_exists", False),
        "backup_reopens": found.get("backup_reopens", False),
        "backup_integrity": found.get("backup_integrity", False),
        "ride_removed": found.get("ride_removed", False),
    }


# --- E5.4.1: the library live on the real DB + the two new routes


def _library_ride_config(name: str) -> RideConfig:
    """Return the store ride config the E5.4.1 scenarios use."""
    return RideConfig(
        name=name,
        event_date=date(2026, 9, 20),
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive local, Store's own contract
        planned_duration_s=21600,
        min_lap_s=1080,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )


def _library_roster() -> Roster:
    """Build the MIXED rider_pooled roster the E5.4.1 scenarios persist.

    One solo entry plus one team of two riders with their own plates
    -- the roster shape ``Store.duplicate_ride`` must copy verbatim
    and ``Store.roster_for`` must rebuild identically.
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(name="Alice", plate="12")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[Rider(name="A. Roy", plate="77"), Rider(name="K. Singh", plate="78")],
    )
    return roster


def _create_library_ride(path: Path, *, name: str, running: bool) -> int:
    """Create a store ride with a saved roster; timing when *running*.

    ``running`` appends start + one crossing so the ride reads RUNNING
    with a recorded lap -- the timing data ``duplicate_ride`` must
    leave out of the copy.
    """
    boot = Store.open(path)
    try:
        ride_id = boot.create_ride(_library_ride_config(name))
        boot.save_roster(ride_id, _library_roster())
        if running:
            boot.append(
                ride_id,
                Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"}),
            )
            boot.append(
                ride_id,
                Event(
                    action="record_crossing",
                    payload={
                        "plate": "12",
                        "entry_id": "12",
                        "lap": 1,
                        "crossed_at": "2026-09-20T10:02:00",
                    },
                ),
            )
    finally:
        boot.close()
    return ride_id


def _running_ride_with_roster(path: Path) -> int:
    """Create a running store ride and a quit-keep-running session.

    The previous session records the ride as running at a clean exit,
    so the launch shows resume_dlg and Continue sets the context's
    ``active_ride_id`` -- what File ▸ Duplicate Ride… reads (E5.4.1).
    """
    boot = Store.open(path)
    try:
        ride_id = boot.create_ride(_library_ride_config("GORBA EPIC 2026"))
        boot.save_roster(ride_id, _library_roster())
        boot.append(
            ride_id,
            Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"}),
        )
    finally:
        boot.close()
    session = Store.open(path, active_ride_id=ride_id)
    session.close_session()
    session.close()
    return ride_id


def _library_live_open_switches_console_context() -> dict[str, Any]:
    """Library Open loads the store ride and swaps the console onto it.

    Launch keeps the demo console (no running ride at the previous
    exit), then the library's Open on the RUNNING store ride must
    switch the console to that ride: status RUNNING and the feed shows
    the persisted crossing -- neither of which the demo console had.
    """
    db_path = _resume_db_path("rc-lib-open-")
    _create_library_ride(db_path, name="GORBA EPIC 2026", running=True)
    store = Store.open(db_path)
    found: dict[str, Any] = {}
    frame: Any = None

    def _open_the_ride() -> None:
        library = wx.Window.FindWindowByName(ids.RIDE_LIBRARY_DLG)
        if library is None:
            return
        harness.select_row(library, ids.RIDES_LIST, 0)
        harness.click(library, pages.WX_ID_OPEN)

    try:
        frame = _build_app_window(store=store)
        frame.Show()
        frame.Layout()
        harness.pump()
        wx.CallAfter(_open_the_ride)
        harness.fire_menu_event(frame, "mi_open_library")
        harness.pump()
        model = harness.find_control(frame, ids.CROSSINGS_LIST).GetModel()
        found["status_label"] = harness.find_control(frame, ids.RIDE_STATUS_LBL).GetLabelText()
        found["feed_rows"] = model.GetCount()
        found["feed_plate"] = (
            model.GetValueByRow(0, feed_model.COL_PLATE) if model.GetCount() > 0 else ""
        )
    finally:
        store.close()
        if frame is not None:
            _close_without_prompt(frame)
    return found


def _library_live_duplicate_appears_as_new_draft() -> dict[str, Any]:  # noqa: PLR0915 -- scripted modal-driving flow: nested probes + store facts, the scenario pattern this file owns
    """Library Duplicate: the copy appears as a DRAFT ride, no timing.

    Drives the library's Duplicate button on a RUNNING source ride:
    the E5.4.1 confirm opens (nested in the library modal), is probed
    for its naming copy + default, OK runs ``Store.duplicate_ride``,
    the library refreshes, and the store facts prove the R-15 copy --
    DRAFT, same roster, zero crossings/cards/audit, fresh seed. The
    library's Status column reads the STORED ``ride.status``, and the
    facade does not sync that column when events are appended (the
    documented E5.4 engine-sync gap, store module docstring), so the
    RUNNING source ride's row reads DRAFT too -- the console, by
    contrast, derives status from the replayed engine (the Open
    scenario asserts RUNNING there).
    """
    db_path = _resume_db_path("rc-lib-dup-")
    source_id = _create_library_ride(db_path, name="GORBA EPIC 2026", running=True)
    store = Store.open(db_path)
    found: dict[str, Any] = {}
    frame: Any = None

    def _drive_duplicate_dialog() -> None:
        dialog = wx.Window.FindWindowByName(ids.DUPLICATE_RIDE_DLG)
        found["duplicate_dlg_shown"] = dialog is not None
        if dialog is None:
            return
        found["duplicate_message"] = harness.find_control(dialog, ids.MESSAGE_LBL).GetLabelText()
        default_item = dialog.GetDefaultItem()
        found["duplicate_default"] = default_item.GetName() if default_item is not None else None
        harness.click(dialog, pages.WX_ID_OK)

    def _record_rows_and_close(library: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        model = harness.find_control(library, ids.RIDES_LIST).GetModel()
        found["rows_after"] = [
            (
                model.GetValueByRow(row, COL_NAME),
                model.GetValueByRow(row, COL_STATUS),
            )
            for row in range(model.GetCount())
        ]
        if not library.IsBeingDeleted():
            library.EndModal(wx.ID_CLOSE)

    def _drive_library() -> None:
        library = wx.Window.FindWindowByName(ids.RIDE_LIBRARY_DLG)
        if library is None:
            return
        harness.select_row(library, ids.RIDES_LIST, 0)
        wx.CallAfter(_drive_duplicate_dialog)
        harness.click(library, ids.DUPLICATE_BTN)
        wx.CallAfter(_record_rows_and_close, library)

    try:
        frame = _build_app_window(store=store)
        frame.Show()
        frame.Layout()
        harness.pump()
        wx.CallAfter(_drive_library)
        harness.fire_menu_event(frame, "mi_open_library")
        harness.pump()

        reopened = Store.open(db_path)
        try:
            rows = reopened.rides()
            found["ride_count"] = len(rows)
            copy = next(row for row in rows if row.id != source_id)
            found["copy_name"] = copy.name
            found["copy_status"] = copy.status.value
            found["copy_entries"] = copy.entries
            found["copy_roster"] = [
                (entry.plate, entry.display_name) for entry in reopened.roster_for(copy.id).entries
            ]
            with sqlite3.connect(str(db_path)) as conn:
                found["copy_crossings"] = conn.execute(
                    "SELECT COUNT(*) FROM crossing WHERE ride_id = ?", (copy.id,)
                ).fetchone()[0]
                found["copy_cards"] = conn.execute(
                    "SELECT COUNT(*) FROM card WHERE ride_id = ?", (copy.id,)
                ).fetchone()[0]
                found["copy_audit"] = conn.execute(
                    "SELECT COUNT(*) FROM audit WHERE ride_id = ?", (copy.id,)
                ).fetchone()[0]
                source_seed = conn.execute(
                    "SELECT rng_seed FROM ride WHERE id = ?", (source_id,)
                ).fetchone()[0]
                copy_seed = conn.execute(
                    "SELECT rng_seed FROM ride WHERE id = ?", (copy.id,)
                ).fetchone()[0]
                found["fresh_seed"] = copy_seed != source_seed
        finally:
            reopened.close()
    finally:
        store.close()
        if frame is not None:
            _close_without_prompt(frame)
    return found


def _duplicate_ride_menu_route_opens_confirm_and_duplicates() -> dict[str, Any]:
    """File ▸ Duplicate Ride… opens the confirm and duplicates (E5.4.1).

    The resume flow (E5.2.2's Continue) sets the context's
    ``active_ride_id``, then firing the duplicate route -- which used
    to hit the E1.4.1 sentinel -- opens ``duplicate_ride_dlg`` naming
    the ride; OK creates the copy.
    """
    db_path = _resume_db_path("rc-dup-route-")
    source_id = _running_ride_with_roster(db_path)
    store = Store.open(db_path)
    found: dict[str, Any] = {}
    frame: Any = None

    def _drive_duplicate_dialog() -> None:
        dialog = wx.Window.FindWindowByName(ids.DUPLICATE_RIDE_DLG)
        found["duplicate_dlg_shown"] = dialog is not None
        if dialog is None:
            return
        found["duplicate_message"] = harness.find_control(dialog, ids.MESSAGE_LBL).GetLabelText()
        default_item = dialog.GetDefaultItem()
        found["duplicate_default"] = default_item.GetName() if default_item is not None else None
        harness.click(dialog, pages.WX_ID_OK)

    def _resume_then_fire_duplicate() -> None:
        resume = wx.Window.FindWindowByName(ids.RESUME_DLG)
        if resume is None:
            return
        harness.click(resume, ids.CONTINUE_BTN)

    try:
        wx.CallAfter(_resume_then_fire_duplicate)
        frame = _build_app_window(store=store)
        frame.Show()
        frame.Layout()
        harness.pump()
        wx.CallAfter(_drive_duplicate_dialog)
        harness.fire_menu_event(frame, "mi_duplicate_ride")
        harness.pump()
        found["status_text"] = frame.GetStatusBar().GetStatusText(0)
        reopened = Store.open(db_path)
        try:
            rows = reopened.rides()
            found["ride_count"] = len(rows)
            copy = next(row for row in rows if row.id != source_id)
            found["copy_name"] = copy.name
            found["copy_status"] = copy.status.value
        finally:
            reopened.close()
    finally:
        store.close()
        if frame is not None:
            _close_without_prompt(frame)
    return found


def _reopen_ride_menu_route_opens_confirm_and_reopens() -> dict[str, Any]:
    """Ride ▸ Reopen Ride opens the confirm and reopens (E5.4.1).

    Resume continues a RUNNING ride, the library Open loads a FINISHED
    ride ("Club poker night"), and the reopen route -- previously the
    E1.4.1 sentinel -- opens ``reopen_ride_dlg`` naming it; OK moves
    the console to REOPENED (spec §3).
    """
    db_path = _resume_db_path("rc-reopen-route-")
    _running_ride_with_roster(db_path)
    boot = Store.open(db_path)
    try:
        finished_id = boot.create_ride(_library_ride_config("Club poker night"))
        boot.save_roster(finished_id, _library_roster())
        boot.append(
            finished_id,
            Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"}),
        )
        boot.append(finished_id, Event(action="finish", payload={}))
    finally:
        boot.close()

    store = Store.open(db_path)
    found: dict[str, Any] = {}
    frame: Any = None

    def _drive_reopen_dialog() -> None:
        dialog = wx.Window.FindWindowByName(ids.REOPEN_RIDE_DLG)
        found["reopen_dlg_shown"] = dialog is not None
        if dialog is None:
            return
        found["reopen_message"] = harness.find_control(dialog, ids.MESSAGE_LBL).GetLabelText()
        harness.click(dialog, pages.WX_ID_OK)

    def _open_finished_ride() -> None:
        library = wx.Window.FindWindowByName(ids.RIDE_LIBRARY_DLG)
        if library is None:
            return
        harness.select_row(library, ids.RIDES_LIST, 1)
        harness.click(library, pages.WX_ID_OPEN)

    def _resume_then_open_finished() -> None:
        resume = wx.Window.FindWindowByName(ids.RESUME_DLG)
        if resume is None:
            return
        harness.click(resume, ids.CONTINUE_BTN)

    try:
        wx.CallAfter(_resume_then_open_finished)
        frame = _build_app_window(store=store)
        frame.Show()
        frame.Layout()
        harness.pump()
        wx.CallAfter(_open_finished_ride)
        harness.fire_menu_event(frame, "mi_open_library")
        harness.pump()
        wx.CallAfter(_drive_reopen_dialog)
        harness.fire_menu_event(frame, "mi_reopen_ride")
        harness.pump()
        found["status_label"] = harness.find_control(frame, ids.RIDE_STATUS_LBL).GetLabelText()
    finally:
        store.close()
        if frame is not None:
            _close_without_prompt(frame)
    return found


def _reopen_ride_route_on_non_finished_refuses() -> dict[str, Any]:
    """Reopen Ride on a RUNNING console: the confirm opens, OK refuses.

    The demo bootstrap's console is RUNNING (never FINISHED), so
    confirming the (real) reopen dialog must surface the engine's
    refusal on the status bar, never crash. Runs in this fresh
    interpreter because the in-process equivalent -- building
    ``main_frame`` in the shared worker, then driving a modal from it
    with a pump -- hit the documented wx native-crash churn (the
    address-reuse hazard main_frame.py's own ``_find`` docstring
    records); a fresh interpreter gets an independent layout.
    """
    found: dict[str, Any] = {}
    frame: Any = None

    def _confirm_reopen() -> None:
        dialog = wx.Window.FindWindowByName(ids.REOPEN_RIDE_DLG)
        found["reopen_dlg_shown"] = dialog is not None
        if dialog is None:
            return
        found["reopen_message"] = harness.find_control(dialog, ids.MESSAGE_LBL).GetLabelText()
        harness.click(dialog, pages.WX_ID_OK)

    try:
        frame = _build_app_window()
        frame.Show()
        frame.Layout()
        harness.pump()
        wx.CallAfter(_confirm_reopen)
        harness.fire_menu_event(frame, "mi_reopen_ride")
        harness.pump()
        found["status_text"] = frame.GetStatusBar().GetStatusText(0)
    finally:
        if frame is not None:
            _close_without_prompt(frame)
    return found


def _csv_import_commit_reads_editor() -> dict[str, Any]:
    """Import clean_pooled.csv via mi_import_csv, then read riders_list.

    The E3.4 E2E proof that the committed roster is the same one the
    editor reads. Runs in this fresh interpreter for a fourth reason:
    measured on macOS CI (PR #9, runs 32554309607 and 32650668444),
    the two riders.xrc dialog loads in sequence (csv_preview_dlg then
    rider_editor_dlg) trigger the documented SIP wrapper-cache
    degradation in the caller's long-lived worker -- the second load
    builds with the whole action staticbox missing and
    ``RiderEditor.__init__`` raises a ``LookupError`` the route
    handler swallows, so the monkeypatched ``run_dialog`` never runs
    and the caller sees ``KeyError: 'plates'``. A fresh interpreter
    gets an independent memory layout (scenario_runner's own retry
    rationale), which is the one measured remedy for that corruption
    (docs/EPIC3-SESSION-SUMMARY.md, Addendum 2).
    """
    fixture = (
        Path(__file__).resolve().parent.parent / "unit" / "fixtures" / "csv" / "clean_pooled.csv"
    )
    original_pick = rider_editor._pick_import_path
    original_run_dialog = dialogs.run_dialog
    frame: Any = None
    try:
        rider_editor._pick_import_path = lambda _parent: fixture

        def _click_import(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001
            harness.click(dialog, "wxID_OK")
            return wx.ID_OK

        dialogs.run_dialog = _click_import
        frame = _build_app_window()
        _fire_menu_event(frame, "mi_import_csv")

        plates: set[str] = set()

        def _capture_plates(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001
            model = harness.find_control(dialog, ids.RIDERS_LIST).GetModel()
            plates.update(model.GetValueByRow(row, 0) for row in range(model.GetCount()))
            return wx.ID_CANCEL

        dialogs.run_dialog = _capture_plates
        _fire_menu_event(frame, "mi_rider_editor")
        return {"plates": sorted(plates)}
    finally:
        rider_editor._pick_import_path = original_pick
        dialogs.run_dialog = original_run_dialog
        if frame is not None:
            # A live main_frame keeps a non-daemon thread alive at
            # interpreter shutdown (measured: the child printed its
            # JSON envelope, then hung until the 50s bound); close it
            # without the quit confirm, exactly like the quit-flow
            # scenarios do.
            _close_without_prompt(frame)


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


def _menu_item_checked(frame: Any, item_id: str) -> bool:  # noqa: ANN401
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
    frame = _build_app_window()
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
            "radio_checked": _menu_item_checked(frame, ids.MI_THEME_DARK),
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
    frame = _build_app_window()
    frame.Show()
    frame.Layout()
    harness.pump()
    try:
        _fire_menu_event(frame, ids.MI_THEME_DARK)
        _fire_menu_event(frame, ids.MI_THEME_LIGHT)
        return {
            "is_dark_after": wx.SystemSettings.GetAppearance().IsDark(),
            "radio_checked": _menu_item_checked(frame, ids.MI_THEME_LIGHT),
            "notice_after": frame.GetStatusBar().GetStatusText(0),
        }
    finally:
        _close_without_prompt(frame)


def _theme_system_reapplies_on_sys_colour_changed() -> dict[str, Any]:
    """Dark then System: a guarded re-apply, bounded (best-effort).

    # logic-coverage-exempt: T-10 -- this passthrough spy targets an
    # internal module function (``theme.apply``), not a true I/O
    # boundary, for the same reason the retired
    # ``_counting_show_notices`` spy was exempted before it: the
    # final ``SystemSettings`` state alone cannot
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
        frame = _build_app_window()
        frame.Show()
        frame.Layout()
        harness.pump()
        try:
            _fire_menu_event(frame, ids.MI_THEME_DARK)
            _fire_menu_event(frame, ids.MI_THEME_SYSTEM)
            return {
                "apply_call_count": len(calls),
                "radio_checked": _menu_item_checked(frame, ids.MI_THEME_SYSTEM),
            }
        finally:
            _close_without_prompt(frame)
    finally:
        theme.apply = original_apply


def _theme_ids_do_not_post_the_stub_notice_and_zoom_applies() -> dict[str, Any]:
    """Theme and zoom ids post no stub notice; zoom applies (E8.1.4).

    Returns the raw post-fire theme notice text too, not only whether
    it changed: MSW's ``CannotChange`` contract means firing the theme
    id *does* change the status bar on Windows (to the documented
    next-launch text), so "unchanged" alone would read as a false
    positive there for the one fact this scenario needs to prove on
    every platform -- that neither the theme nor the zoom id posts the
    generic ``route.label — not yet implemented`` stub. Zoom applies:
    the fired radio is checked and the settings file records 110.
    """
    frame = _build_app_window()
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
            "zoom_notice_after": after_zoom,
            "zoom_radio_checked": _menu_item_checked(frame, ids.MI_ZOOM_110),
            "zoom_percent_after": load_settings(_SCENARIO_SETTINGS_PATH).zoom_percent,
        }
    finally:
        _close_without_prompt(frame)


# --- E4.4.1/E4.4.2: the live console on a real engine -----------------


class _ScenarioClock:
    """An advanceable datetime clock for a live ride (R-30 fake)."""

    def __init__(self, start: object) -> None:
        """Start at *start* (a datetime)."""
        self._now = start

    def __call__(self) -> object:
        """Return the current scenario time."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the clock forward by *seconds*."""
        self._now = self._now + timedelta(seconds=seconds)  # type: ignore[operator]


def _live_console_parts(
    resource: object,
    *,
    min_lap_s: int = 1,
    plates: tuple[str, ...] = ("12", "34"),
) -> tuple[Any, MainFrame, RideEngine, _ScenarioClock]:
    """Build a RUNNING live console: real engine + MainFrame, wired.

    Returns ``(window, console, engine, clock)``. The console is wired
    exactly as the app bootstrap wires it (``wire_entry`` +
    ``wire_console`` + ``set_state``), so the scenarios below drive the
    real bindings, not the presenter in isolation.
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    for plate in plates:
        roster.create_solo_entry(name=f"Rider {plate}", plate=plate)
    config = RideConfig(
        name="GORBA EPIC 2026",
        event_date=date(2026, 9, 20),
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001
        planned_duration_s=21600,
        min_lap_s=min_lap_s,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    clock = _ScenarioClock(config.planned_start)
    engine = RideEngine(config=config, shoe=shoe, clock=clock, roster=roster)
    engine.start()
    source = EngineDataSource(engine, roster)

    window = harness.load_window(resource, ids.MAIN_FRAME, frame=True)
    window.Show()
    window.Layout()
    harness.pump()
    console = MainFrame(window, data_source=source, resource=resource)
    presenter = ConsolePresenter(console, engine=engine, source=source)
    console.wire_entry(presenter.on_plate_entered)
    console.wire_console(presenter)
    console.set_state(source.ride_status())
    return window, console, engine, clock


def _set_checkbox(window: Any, name: str, *, value: bool) -> None:  # noqa: ANN401
    """Set *name*'s value and post the event a real click would fire.

    ``harness`` has helpers for buttons, choices and radios but not
    checkboxes; a plain ``SetValue`` fires no ``EVT_CHECKBOX`` (the
    same silence its other helpers document), so the event is posted
    directly -- this module's one working mechanism.
    """
    control = harness.find_control(window, name)
    control.SetValue(value)
    event = wx.CommandEvent(wx.EVT_CHECKBOX.typeId, control.GetId())
    event.SetEventObject(control)
    control.GetEventHandler().ProcessEvent(event)
    harness.pump()


def _live_typed_plate_appears_in_feed() -> dict[str, Any]:
    """Type a plate + Enter; the feed shows it with a card chip."""
    resource = harness.load_xrc_resources()
    window, console, _engine, clock = _live_console_parts(resource, min_lap_s=1)
    try:
        plate_input = harness.find_control(window, ids.PLATE_INPUT)
        focus_calls = _spy_on_set_focus(plate_input)  # after the bootstrap's own focus_entry()
        clock.advance(10)  # a 10 s lap is never short under min_lap_s=1
        plate_input.SetValue("12")
        _post_text_enter(plate_input)
        model = harness.find_control(window, ids.CROSSINGS_LIST).GetModel()
        card_bitmap = model.GetValueByRow(0, feed_model.COL_CARD)
        return {
            "feed_plates": [
                model.GetValueByRow(row, feed_model.COL_PLATE) for row in range(model.GetCount())
            ],
            "card_chip_ok": bool(card_bitmap is not None and card_bitmap.IsOk()),
            "crossings_label": harness.find_control(
                window, ids.CROSSINGS_COUNT_LBL
            ).GetLabelText(),
            "field_cleared": plate_input.GetValue() == "",
            "focused": len(focus_calls) > 0,
        }
    finally:
        del console
        harness.close_window(window)


def _live_flagged_crossing_row_is_bold() -> dict[str, Any]:
    """Record a short lap; its feed row must read bold (R-34)."""
    resource = harness.load_xrc_resources()
    window, console, engine, _clock = _live_console_parts(resource, min_lap_s=60)
    try:
        plate_input = harness.find_control(window, ids.PLATE_INPUT)
        plate_input.SetValue("12")
        _post_text_enter(plate_input)  # 0 s lap < 60 s min lap -> flagged, card held
        model = harness.find_control(window, ids.CROSSINGS_LIST).GetModel()
        attr = wx.dataview.DataViewItemAttr()
        attr_set = model.GetAttrByRow(0, feed_model.COL_TIME, attr)
        card_bitmap = model.GetValueByRow(0, feed_model.COL_CARD)
        return {
            "row_bold": bool(attr_set and attr.GetBold()),
            "card_chip_ok": bool(card_bitmap is not None and card_bitmap.IsOk()),
            "held_count": len(engine.held_crossings()),
        }
    finally:
        del console
        harness.close_window(window)


def _live_arm_stop_confirm_flow() -> dict[str, Any]:
    """R-35: arm enables Stop; the confirm stops the engine, locks."""
    resource = harness.load_xrc_resources()
    window, console, engine, _clock = _live_console_parts(resource, min_lap_s=1)
    try:
        stop_btn = harness.find_control(window, ids.STOP_BTN)
        arm_chk = harness.find_control(window, ids.ARM_STOP_CHK)
        plate_input = harness.find_control(window, ids.PLATE_INPUT)
        before_arm = stop_btn.IsEnabled()
        _set_checkbox(window, ids.ARM_STOP_CHK, value=True)
        while_armed = stop_btn.IsEnabled()

        def _click_stop_ok() -> None:
            dialog = wx.Window.FindWindowByName(ids.STOP_CONFIRM_DLG)
            harness.click(dialog, "wxID_OK")

        wx.CallAfter(_click_stop_ok)
        harness.click(window, ids.STOP_BTN)  # opens the confirm; CallAfter clicks Stop ride

        after_use = stop_btn.IsEnabled()
        arm_after = arm_chk.GetValue()
        entry_enabled = plate_input.IsEnabled()
        refused_reason = engine.record_crossing("12").reason
        return {
            "stop_enabled_before_arm": before_arm,
            "stop_enabled_while_armed": while_armed,
            "stop_enabled_after_confirm": after_use,
            "arm_checked_after_confirm": arm_after,
            "plate_enabled_after_stop": entry_enabled,
            "refused_reason": refused_reason,
        }
    finally:
        del console
        harness.close_window(window)


def _frame_geometry(frame: Any) -> list[int]:  # noqa: ANN401
    """Return the frame's (x, y, width, height) as a JSON-safe list."""
    position = frame.GetPosition()
    size = frame.GetSize()
    return [position.x, position.y, size.width, size.height]


def _settings_persistence_round_trip() -> dict[str, Any]:
    """Save every setting in one app run; relaunch; read it all back.

    E8.1.1's end-to-end proof (runs only in the VM): the bootstrap
    loads the per-user settings file at startup and applies what has
    live paths -- appearance radio, sound mute, hide-times columns,
    splitter sash, frame geometry -- and the layout seams persist the
    sash/geometry on change and on close. Runs against a temp file,
    never the real user config dir.

    The sash is mutated through the direct ``persist_layout`` seam (wx
    only fires the sash event for genuine user drags, measured) and the
    geometry through real ``Move``/``SetSize`` calls; the explicit
    flush after them makes the saved file deterministic before the
    close-save also runs.
    """
    with tempfile.TemporaryDirectory(prefix="rc-settings-") as tmp:
        settings_path = Path(tmp) / "settings.json"
        target = AppSettings(
            appearance=theme.ThemeMode.DARK.value,
            sound_on=False,
            hide_times=True,
            zoom_percent=140,
            splitter_sash=320,
            window_geometry=(40, 60, 1200, 800),
        )
        save_settings(target, settings_path)

        # First run: the bootstrap loads the saved file and applies it.
        frame = _build_app_window(settings_path=settings_path)
        frame.Show()
        frame.Layout()
        harness.pump()
        try:
            console = frame.console
            splitter = harness.find_control(frame, ids.MAIN_SPLITTER)
            crossings_list = harness.find_control(frame, ids.CROSSINGS_LIST)
            applied = {
                "applied_dark_radio": _menu_item_checked(frame, ids.MI_THEME_DARK),
                "applied_sound_muted": sound._default_player._muted,
                "applied_hide_times_columns": _visible_column_titles(crossings_list),
                "applied_sash": splitter.GetSashPosition(),
                "applied_geometry": _frame_geometry(frame),
            }

            splitter.SetSashPosition(420)
            frame.Move((90, 110))
            frame.SetSize((1250, 860))
            harness.pump()
            console.persist_layout()
            saved_after_run1 = load_settings(settings_path)
            saved_sash = saved_after_run1.splitter_sash
            saved_geometry = (
                list(saved_after_run1.window_geometry)
                if saved_after_run1.window_geometry is not None
                else None
            )
        finally:
            _close_without_prompt(frame)

        # Relaunch: a fresh build reads the file the first run saved.
        frame2 = _build_app_window(settings_path=settings_path)
        frame2.Show()
        frame2.Layout()
        harness.pump()
        try:
            splitter2 = harness.find_control(frame2, ids.MAIN_SPLITTER)
            relaunched = {
                "relaunch_sash": splitter2.GetSashPosition(),
                "relaunch_geometry": _frame_geometry(frame2),
            }
        finally:
            _close_without_prompt(frame2)

        return {
            **applied,
            "saved_sash_after_run1": saved_sash,
            "saved_geometry_after_run1": saved_geometry,
            **relaunched,
        }


def _settings_dialog_renders_persisted_values() -> dict[str, Any]:
    """Open Settings; the dialog renders the persisted values (E8.1.2).

    Pre-saves a full settings set (light / sound off / hide-times on /
    zoom 130), builds the app, opens settings_dlg through the File ▸
    Settings… route, and reads the rendered control states. Closes via
    Cancel: rendering must itself change nothing.
    """
    with tempfile.TemporaryDirectory(prefix="rc-settings-dlg-") as tmp:
        settings_path = Path(tmp) / "settings.json"
        save_settings(
            AppSettings(
                appearance=theme.ThemeMode.LIGHT.value,
                sound_on=False,
                hide_times=True,
                zoom_percent=130,
                splitter_sash=None,
                window_geometry=None,
            ),
            settings_path,
        )
        frame = _build_app_window(settings_path=settings_path)
        frame.Show()
        frame.Layout()
        harness.pump()
        found: dict[str, Any] = {}

        def _read_and_cancel() -> None:
            dialog = wx.Window.FindWindowByName(ids.SETTINGS_DLG)
            found["dlg_shown"] = dialog is not None
            if dialog is None:
                return
            found["rendered_system"] = harness.find_control(
                dialog, ids.APPEARANCE_SYSTEM_RADIO
            ).GetValue()
            found["rendered_light"] = harness.find_control(
                dialog, ids.APPEARANCE_LIGHT_RADIO
            ).GetValue()
            found["rendered_dark"] = harness.find_control(
                dialog, ids.APPEARANCE_DARK_RADIO
            ).GetValue()
            found["rendered_sound"] = harness.find_control(dialog, ids.SOUND_CHK).GetValue()
            found["rendered_hide_times"] = harness.find_control(
                dialog, ids.HIDE_TIMES_CHK
            ).GetValue()
            found["rendered_zoom_selection"] = harness.find_control(
                dialog, ids.ZOOM_CHOICE
            ).GetSelection()
            harness.click(dialog, pages.WX_ID_CANCEL)

        try:
            wx.CallAfter(_read_and_cancel)
            harness.fire_menu_event(frame, "wxID_PREFERENCES")
            harness.pump()
        finally:
            _close_without_prompt(frame)
        return found


def _settings_dialog_ok_applies_and_persists_dark() -> dict[str, Any]:  # noqa: PLR0915 -- two app runs (bootstrap + relaunch), each with a modal-driver closure
    """Toggle Dark in Settings, OK: applied, persisted, relaunched.

    E8.1.2's appearance-mirror proof. Pre-saves a LIGHT set so the
    toggle is visible; opens Settings, sets the Dark radio (explicitly
    clearing the others -- a programmatic ``SetValue`` may not
    auto-uncheck the group) plus sound-off and hide-times-on, clicks
    OK; reads the live appearance, the View-menu radio, the sound
    mute, the hide-times columns and the saved file. A second build
    with the same path re-opens Settings and the Dark radio renders
    checked.
    """
    with tempfile.TemporaryDirectory(prefix="rc-settings-ok-") as tmp:
        settings_path = Path(tmp) / "settings.json"
        save_settings(
            AppSettings(
                appearance=theme.ThemeMode.LIGHT.value,
                sound_on=True,
                hide_times=False,
                zoom_percent=100,
                splitter_sash=None,
                window_geometry=None,
            ),
            settings_path,
        )
        frame = _build_app_window(settings_path=settings_path)
        frame.Show()
        frame.Layout()
        harness.pump()
        found: dict[str, Any] = {}

        def _drive_ok() -> None:
            dialog = wx.Window.FindWindowByName(ids.SETTINGS_DLG)
            found["dlg_shown"] = dialog is not None
            if dialog is None:
                return
            harness.find_control(dialog, ids.APPEARANCE_SYSTEM_RADIO).SetValue(False)  # noqa: FBT003 -- wx API takes a positional bool
            harness.find_control(dialog, ids.APPEARANCE_LIGHT_RADIO).SetValue(False)  # noqa: FBT003 -- wx API takes a positional bool
            harness.find_control(dialog, ids.APPEARANCE_DARK_RADIO).SetValue(True)  # noqa: FBT003 -- wx API takes a positional bool
            harness.find_control(dialog, ids.SOUND_CHK).SetValue(False)  # noqa: FBT003 -- wx API takes a positional bool
            harness.find_control(dialog, ids.HIDE_TIMES_CHK).SetValue(True)  # noqa: FBT003 -- wx API takes a positional bool
            harness.click(dialog, pages.WX_ID_OK)

        try:
            wx.CallAfter(_drive_ok)
            harness.fire_menu_event(frame, "wxID_PREFERENCES")
            harness.pump()
            found["is_dark_after"] = wx.SystemSettings.GetAppearance().IsDark()
            found["menu_dark_checked"] = _menu_item_checked(frame, ids.MI_THEME_DARK)
            found["sound_muted_after"] = sound._default_player._muted
            found["hide_times_columns"] = _visible_column_titles(
                harness.find_control(frame, ids.CROSSINGS_LIST)
            )
            saved = load_settings(settings_path)
            found["saved_appearance"] = saved.appearance
            found["saved_sound_on"] = saved.sound_on
            found["saved_hide_times"] = saved.hide_times
        finally:
            _close_without_prompt(frame)

        # Relaunch: the persisted appearance renders in a fresh dialog.
        frame2 = _build_app_window(settings_path=settings_path)
        frame2.Show()
        frame2.Layout()
        harness.pump()

        def _read_relaunch() -> None:
            dialog = wx.Window.FindWindowByName(ids.SETTINGS_DLG)
            found["relaunch_dlg_shown"] = dialog is not None
            if dialog is None:
                return
            found["relaunch_dark"] = harness.find_control(
                dialog, ids.APPEARANCE_DARK_RADIO
            ).GetValue()
            harness.click(dialog, pages.WX_ID_CANCEL)

        try:
            wx.CallAfter(_read_relaunch)
            harness.fire_menu_event(frame2, "wxID_PREFERENCES")
            harness.pump()
        finally:
            _close_without_prompt(frame2)
        return found


def _settings_dialog_cancel_applies_nothing() -> dict[str, Any]:
    """Toggle Dark in Settings, Cancel: nothing applied, nothing saved.

    E8.1.2's cancel half. Pre-saves a LIGHT set; opens Settings, flips
    Dark + sound off, and clicks Cancel. Reads the live appearance
    (unchanged), the View-menu radio (still light), the sound mute
    (still on) and the saved file (still light/on).
    """
    with tempfile.TemporaryDirectory(prefix="rc-settings-cancel-") as tmp:
        settings_path = Path(tmp) / "settings.json"
        save_settings(
            AppSettings(
                appearance=theme.ThemeMode.LIGHT.value,
                sound_on=True,
                hide_times=False,
                zoom_percent=100,
                splitter_sash=None,
                window_geometry=None,
            ),
            settings_path,
        )
        frame = _build_app_window(settings_path=settings_path)
        frame.Show()
        frame.Layout()
        harness.pump()
        found: dict[str, Any] = {}
        was_dark = wx.SystemSettings.GetAppearance().IsDark()

        def _drive_cancel() -> None:
            dialog = wx.Window.FindWindowByName(ids.SETTINGS_DLG)
            found["dlg_shown"] = dialog is not None
            if dialog is None:
                return
            harness.find_control(dialog, ids.APPEARANCE_DARK_RADIO).SetValue(True)  # noqa: FBT003 -- wx API takes a positional bool
            harness.find_control(dialog, ids.SOUND_CHK).SetValue(False)  # noqa: FBT003 -- wx API takes a positional bool
            harness.click(dialog, pages.WX_ID_CANCEL)

        try:
            wx.CallAfter(_drive_cancel)
            harness.fire_menu_event(frame, "wxID_PREFERENCES")
            harness.pump()
            found["appearance_unchanged"] = wx.SystemSettings.GetAppearance().IsDark() == was_dark
            found["menu_dark_checked"] = _menu_item_checked(frame, ids.MI_THEME_DARK)
            found["sound_muted_after"] = sound._default_player._muted
            saved = load_settings(settings_path)
            found["saved_appearance"] = saved.appearance
            found["saved_sound_on"] = saved.sound_on
        finally:
            _close_without_prompt(frame)
        return found


def _hide_times_view_menu_mirror_round_trip() -> dict[str, Any]:
    """mi_hide_times toggles live, mirrors Settings, survives relaunch.

    E8.1.3's end-to-end proof. Starts with hide-times OFF; toggles ON
    via the View menu (the Lap time/Total columns hide live, the clock
    stays, the check item ticks, the file updates); opens Settings (the
    checkbox mirrors) and unchecks it + OK (the columns return, the
    menu unticks, the file updates -- the reverse mirror); toggles ON
    again; relaunches and reads the persisted hidden columns and the
    ticked menu.
    """
    with tempfile.TemporaryDirectory(prefix="rc-hide-times-") as tmp:
        settings_path = Path(tmp) / "settings.json"
        save_settings(
            AppSettings(
                appearance=theme.ThemeMode.SYSTEM.value,
                sound_on=True,
                hide_times=False,
                zoom_percent=100,
                splitter_sash=None,
                window_geometry=None,
            ),
            settings_path,
        )
        frame = _build_app_window(settings_path=settings_path)
        frame.Show()
        frame.Layout()
        harness.pump()
        found: dict[str, Any] = {}
        try:
            crossings = harness.find_control(frame, ids.CROSSINGS_LIST)
            found["before_columns"] = _visible_column_titles(crossings)
            found["clock_shown_before"] = harness.find_control(
                frame, ids.CLOCK_ELAPSED_LBL
            ).IsShown()
            found["menu_checked_before"] = _menu_item_checked(frame, ids.MI_HIDE_TIMES)

            # Toggle ON via the View menu: hide live, clock stays, tick.
            harness.fire_menu_event(frame, ids.MI_HIDE_TIMES)
            found["after_on_columns"] = _visible_column_titles(crossings)
            found["clock_shown_after_on"] = harness.find_control(
                frame, ids.CLOCK_ELAPSED_LBL
            ).IsShown()
            found["menu_checked_after_on"] = _menu_item_checked(frame, ids.MI_HIDE_TIMES)
            found["saved_hide_times_after_on"] = load_settings(settings_path).hide_times

            # The mirror from the Settings dialog: checkbox checked, and
            # unchecking it + OK reverts the console and the menu.
            def _uncheck_in_settings() -> None:
                dialog = wx.Window.FindWindowByName(ids.SETTINGS_DLG)
                found["settings_dlg_shown"] = dialog is not None
                if dialog is None:
                    return
                found["settings_checkbox_after_on"] = harness.find_control(
                    dialog, ids.HIDE_TIMES_CHK
                ).GetValue()
                harness.find_control(dialog, ids.HIDE_TIMES_CHK).SetValue(False)  # noqa: FBT003 -- wx API takes a positional bool
                harness.click(dialog, pages.WX_ID_OK)

            wx.CallAfter(_uncheck_in_settings)
            harness.fire_menu_event(frame, "wxID_PREFERENCES")
            harness.pump()
            found["after_settings_off_columns"] = _visible_column_titles(crossings)
            found["menu_checked_after_off"] = _menu_item_checked(frame, ids.MI_HIDE_TIMES)
            found["saved_hide_times_after_off"] = load_settings(settings_path).hide_times

            # Toggle ON again so the relaunch check reads hidden.
            harness.fire_menu_event(frame, ids.MI_HIDE_TIMES)
            found["saved_hide_times_before_relaunch"] = load_settings(settings_path).hide_times
        finally:
            _close_without_prompt(frame)

        frame2 = _build_app_window(settings_path=settings_path)
        frame2.Show()
        frame2.Layout()
        harness.pump()
        try:
            crossings2 = harness.find_control(frame2, ids.CROSSINGS_LIST)
            found["relaunch_columns"] = _visible_column_titles(crossings2)
            found["relaunch_menu_checked"] = _menu_item_checked(frame2, ids.MI_HIDE_TIMES)
        finally:
            _close_without_prompt(frame2)
        return found


def _zoom_view_menu_applies_live_and_boundaries() -> dict[str, Any]:
    """mi_zoom_* scale console fonts live; 90/150 bound the ladder.

    E8.1.4's menu half. Reads the ride-status label's point size at
    100%, fires the zoom radios, and reports each scaled size plus the
    radio ticked right after each fire.
    """
    with tempfile.TemporaryDirectory(prefix="rc-zoom-menu-") as tmp:
        settings_path = Path(tmp) / "settings.json"
        save_settings(
            AppSettings(
                appearance=theme.ThemeMode.SYSTEM.value,
                sound_on=True,
                hide_times=False,
                zoom_percent=100,
                splitter_sash=None,
                window_geometry=None,
            ),
            settings_path,
        )
        frame = _build_app_window(settings_path=settings_path)
        frame.Show()
        frame.Layout()
        harness.pump()
        found: dict[str, Any] = {}
        try:
            status_lbl = harness.find_control(frame, ids.RIDE_STATUS_LBL)
            found["base_pt"] = status_lbl.GetFont().GetPointSize()
            harness.fire_menu_event(frame, ids.MI_ZOOM_120)
            found["pt_at_120"] = status_lbl.GetFont().GetPointSize()
            found["radio_120_checked"] = _menu_item_checked(frame, ids.MI_ZOOM_120)
            harness.fire_menu_event(frame, ids.MI_ZOOM_90)
            found["pt_at_90"] = status_lbl.GetFont().GetPointSize()
            found["radio_90_checked"] = _menu_item_checked(frame, ids.MI_ZOOM_90)
            harness.fire_menu_event(frame, ids.MI_ZOOM_150)
            found["pt_at_150"] = status_lbl.GetFont().GetPointSize()
            found["radio_150_checked"] = _menu_item_checked(frame, ids.MI_ZOOM_150)
            found["saved_zoom"] = load_settings(settings_path).zoom_percent
        finally:
            _close_without_prompt(frame)
        return found


def _zoom_settings_mirror_and_dialog() -> dict[str, Any]:
    """Mirror the View radio in the Settings choice; dialogs scale.

    E8.1.4's mirror + dialog half. Opens Settings at 100% to capture
    the zoom_choice's base font; zooms to 120 via the View menu; opens
    Settings again (the choice shows 120 and its font is scaled),
    changes the choice to 130, OK -- the console scales to 130 and the
    View radio re-checks to 130.
    """
    with tempfile.TemporaryDirectory(prefix="rc-zoom-mirror-") as tmp:
        settings_path = Path(tmp) / "settings.json"
        save_settings(
            AppSettings(
                appearance=theme.ThemeMode.SYSTEM.value,
                sound_on=True,
                hide_times=False,
                zoom_percent=100,
                splitter_sash=None,
                window_geometry=None,
            ),
            settings_path,
        )
        frame = _build_app_window(settings_path=settings_path)
        frame.Show()
        frame.Layout()
        harness.pump()
        found: dict[str, Any] = {}

        def _read_choice_base() -> None:
            dialog = wx.Window.FindWindowByName(ids.SETTINGS_DLG)
            found["dlg_shown"] = dialog is not None
            if dialog is None:
                return
            found["choice_base_pt"] = (
                harness.find_control(dialog, ids.ZOOM_CHOICE).GetFont().GetPointSize()
            )
            harness.click(dialog, pages.WX_ID_CANCEL)

        try:
            status_lbl = harness.find_control(frame, ids.RIDE_STATUS_LBL)
            found["base_pt"] = status_lbl.GetFont().GetPointSize()

            wx.CallAfter(_read_choice_base)
            harness.fire_menu_event(frame, "wxID_PREFERENCES")
            harness.pump()

            harness.fire_menu_event(frame, ids.MI_ZOOM_120)
            found["pt_after_menu_120"] = status_lbl.GetFont().GetPointSize()
            found["radio_120_checked"] = _menu_item_checked(frame, ids.MI_ZOOM_120)

            def _drive_settings_130() -> None:
                dialog = wx.Window.FindWindowByName(ids.SETTINGS_DLG)
                found["dlg_shown_2"] = dialog is not None
                if dialog is None:
                    return
                found["choice_selection_at_120"] = harness.find_control(
                    dialog, ids.ZOOM_CHOICE
                ).GetSelection()
                found["choice_pt_at_120"] = (
                    harness.find_control(dialog, ids.ZOOM_CHOICE).GetFont().GetPointSize()
                )
                harness.find_control(dialog, ids.ZOOM_CHOICE).SetSelection(ZOOM_LADDER.index(130))
                harness.click(dialog, pages.WX_ID_OK)

            wx.CallAfter(_drive_settings_130)
            harness.fire_menu_event(frame, "wxID_PREFERENCES")
            harness.pump()
            found["pt_after_settings_130"] = status_lbl.GetFont().GetPointSize()
            found["radio_130_checked"] = _menu_item_checked(frame, ids.MI_ZOOM_130)
            found["saved_zoom"] = load_settings(settings_path).zoom_percent
        finally:
            _close_without_prompt(frame)
        return found


def _zoom_survives_relaunch() -> dict[str, Any]:
    """Zoom 140 set via the View menu survives a relaunch (E8.1.4)."""
    with tempfile.TemporaryDirectory(prefix="rc-zoom-relaunch-") as tmp:
        settings_path = Path(tmp) / "settings.json"
        save_settings(
            AppSettings(
                appearance=theme.ThemeMode.SYSTEM.value,
                sound_on=True,
                hide_times=False,
                zoom_percent=100,
                splitter_sash=None,
                window_geometry=None,
            ),
            settings_path,
        )
        frame = _build_app_window(settings_path=settings_path)
        frame.Show()
        frame.Layout()
        harness.pump()
        found: dict[str, Any] = {}
        try:
            status_lbl = harness.find_control(frame, ids.RIDE_STATUS_LBL)
            found["base_pt"] = status_lbl.GetFont().GetPointSize()
            harness.fire_menu_event(frame, ids.MI_ZOOM_140)
            found["saved_zoom_before_relaunch"] = load_settings(settings_path).zoom_percent
        finally:
            _close_without_prompt(frame)

        frame2 = _build_app_window(settings_path=settings_path)
        frame2.Show()
        frame2.Layout()
        harness.pump()
        try:
            status_lbl2 = harness.find_control(frame2, ids.RIDE_STATUS_LBL)
            found["relaunch_pt"] = status_lbl2.GetFont().GetPointSize()
            found["relaunch_radio_140_checked"] = _menu_item_checked(frame2, ids.MI_ZOOM_140)
        finally:
            _close_without_prompt(frame2)
        return found


def _send_escape(dialog: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Post a real Escape ``CHAR_HOOK`` at *dialog* (proven idiom).

    The same event ``test_dialog_behavior.py``'s ``_send_escape``
    posts: it triggers wx's built-in Escape handling (which ends the
    modal with the ``SetEscapeId`` button) without needing OS-level key
    focus, which ``UIActionSimulator`` cannot deliver in this harness.
    """
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(wx.WXK_ESCAPE)
    dialog.GetEventHandler().ProcessEvent(event)


def _shortcuts_dialog_route_shows_the_accelerator_table() -> dict[str, Any]:
    """Open Help ▸ Keyboard Shortcuts; the dialog lists the table rows.

    E8.2.1's route half: firing ``mi_shortcuts`` opens ``shortcuts_dlg``
    whose ``shortcuts_list`` renders one Key | Action row per
    ``ACCELERATOR_TABLE`` entry in table order. Escape closes it via
    ``wire_close_button``'s ``wxID_CLOSE`` binding, and ``_open_target``
    destroys it.
    """
    frame = _build_app_window()
    frame.Show()
    frame.Layout()
    harness.pump()
    found: dict[str, Any] = {}

    def _read_rows_and_escape() -> None:
        dialog = wx.Window.FindWindowByName(ids.SHORTCUTS_DLG)
        found["dlg_shown"] = dialog is not None
        if dialog is None:
            return
        list_ctrl = harness.find_control(dialog, ids.SHORTCUTS_LIST)
        model = list_ctrl.GetModel()
        found["row_count"] = model.GetCount()
        found["rows"] = [
            [model.GetValueByRow(row, col) for col in range(2)] for row in range(model.GetCount())
        ]
        found["column_titles"] = [
            list_ctrl.GetColumn(index).GetTitle() for index in range(model.GetColumnCount())
        ]
        _send_escape(dialog)

    try:
        wx.CallAfter(_read_rows_and_escape)
        harness.fire_menu_event(frame, ids.MI_SHORTCUTS)
        harness.pump()
        found["dialog_destroyed"] = wx.Window.FindWindowByName(ids.SHORTCUTS_DLG) is None
    finally:
        _close_without_prompt(frame)
    return found


def _shortcuts_dialog_renders_injected_rows() -> dict[str, Any]:
    """Construct ShortcutsDialog with a fake row; it must appear.

    The ``rows`` constructor seam proves the dialog renders its input
    rather than hard-coding the real four: an injected fake row shows
    alongside a real table row.
    """
    found: dict[str, Any] = {}
    resource = harness.load_xrc_resources()
    window = harness.load_window(resource, ids.SHORTCUTS_DLG, frame=False)
    try:
        view = ShortcutsDialog(
            window,
            rows=(
                ACCELERATOR_TABLE[0],
                Accelerator(key="Ctrl+Alt+K", action="Fake action", menu_item_id=None),
            ),
        )
        model = view.shortcuts_list.GetModel()
        found["row_count"] = model.GetCount()
        found["rows"] = [
            [model.GetValueByRow(row, col) for col in range(2)] for row in range(model.GetCount())
        ]
    finally:
        harness.close_window(window)
    return found


_SCENARIOS: dict[str, Callable[[], dict[str, Any]]] = {
    "sash_round_trip": _sash_round_trip,
    "settings_persistence_round_trip": _settings_persistence_round_trip,
    "settings_dialog_renders_persisted_values": _settings_dialog_renders_persisted_values,
    "settings_dialog_ok_applies_and_persists_dark": (
        _settings_dialog_ok_applies_and_persists_dark
    ),
    "settings_dialog_cancel_applies_nothing": _settings_dialog_cancel_applies_nothing,
    "hide_times_view_menu_mirror_round_trip": _hide_times_view_menu_mirror_round_trip,
    "zoom_view_menu_applies_live_and_boundaries": _zoom_view_menu_applies_live_and_boundaries,
    "zoom_settings_mirror_and_dialog": _zoom_settings_mirror_and_dialog,
    "zoom_survives_relaunch": _zoom_survives_relaunch,
    "shortcuts_dialog_route_shows_the_accelerator_table": (
        _shortcuts_dialog_route_shows_the_accelerator_table
    ),
    "shortcuts_dialog_renders_injected_rows": _shortcuts_dialog_renders_injected_rows,
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
    "exit_running_dlg_probe_and_cancel": _exit_running_dlg_probe_and_cancel,
    "finish_first_routes_to_the_finish_flow": _finish_first_routes_to_the_finish_flow,
    "quit_keep_running_writes_closed_at_and_stays_running": (
        _quit_keep_running_writes_closed_at_and_stays_running
    ),
    "resume_dlg_quit_wording_shows": _resume_dlg_quit_wording_shows,
    "resume_dlg_crash_wording_shows": _resume_dlg_crash_wording_shows,
    "resume_continue_loads_ride_with_elapsed": _resume_continue_loads_ride_with_elapsed,
    "resume_library_opens_ride_library": _resume_library_opens_ride_library,
    "resume_reopened_ride_shows_reopened_infobar": _resume_reopened_ride_shows_reopened_infobar,
    "delete_ride_dlg_backup_written_before_delete": (
        _delete_ride_dlg_backup_written_before_delete
    ),
    "library_live_open_switches_console_context": (_library_live_open_switches_console_context),
    "library_live_duplicate_appears_as_new_draft": (_library_live_duplicate_appears_as_new_draft),
    "duplicate_ride_menu_route_opens_confirm_and_duplicates": (
        _duplicate_ride_menu_route_opens_confirm_and_duplicates
    ),
    "reopen_ride_menu_route_opens_confirm_and_reopens": (
        _reopen_ride_menu_route_opens_confirm_and_reopens
    ),
    "reopen_ride_route_on_non_finished_refuses": _reopen_ride_route_on_non_finished_refuses,
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
    "csv_import_commit_reads_editor": _csv_import_commit_reads_editor,
    "theme_dark_applies_at_runtime": _theme_dark_applies_at_runtime,
    "theme_light_round_trip": _theme_light_round_trip,
    "theme_system_reapplies_on_sys_colour_changed": (
        _theme_system_reapplies_on_sys_colour_changed
    ),
    "theme_ids_do_not_post_the_stub_notice_and_zoom_applies": (
        _theme_ids_do_not_post_the_stub_notice_and_zoom_applies
    ),
    "live_typed_plate_appears_in_feed": _live_typed_plate_appears_in_feed,
    "live_flagged_crossing_row_is_bold": _live_flagged_crossing_row_is_bold,
    "live_arm_stop_confirm_flow": _live_arm_stop_confirm_flow,
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
    bound = threading.Timer(
        scenario_runner.SCENARIO_CHILD_BOUND_SECONDS + 2, os._exit, args=(124,)
    )
    # A non-daemon timer keeps the interpreter's shutdown join alive
    # for the whole bound (measured: every healthy scenario child hung
    # ~bound-seconds at exit). Daemon means it fires only when the
    # process is genuinely still alive -- i.e. hung.
    bound.daemon = True
    bound.start()
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
