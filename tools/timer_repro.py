# SPDX-License-Identifier: GPL-3.0-only
"""Minimal repro for the dangling-tick-timer segfault (tools).

Mirrors what the app's teardown does: build the main frame (which
starts the 1 s tick wx.Timer in main_frame.wire_console), destroy the
frame, then pump via wx.SafeYield() for longer than the timer period so
the native timer fires against the freed frame.

Hypothesis: destroying the frame does not stop _tick_timer, so the
next SafeYield dispatches wxTimerImpl::SendEvent -> SafelyProcessEvent
on freed memory (the measured crash frame).
"""

import faulthandler
import gc
import sys
import time
from typing import Any

import wx

from rivercrossing.ui import app as app_module

faulthandler.enable()

# Bounded idle drain, as test_app_menu_quit.py's own _drain does: wx
# frees a Destroy()ed top-level window only during idle processing.
_DRAIN_ROUNDS = 5


def _flush_deferred_deletions(app: wx.App) -> None:
    """Drive idle so a destroyed top-level window is actually reaped.

    A throwaway event loop is created only when no loop is active (this
    tool never enters MainLoop).
    """
    wx.SafeYield()
    loop = wx.EventLoopBase.GetActive()
    activator: wx.EventLoopActivator | None = None
    if loop is None:
        loop = app.GetTraits().CreateEventLoop()
        activator = wx.EventLoopActivator(loop)
    loop.YieldFor(wx.EVT_CATEGORY_ALL)
    for _ in range(_DRAIN_ROUNDS):
        if not loop.ProcessIdle():
            break
    del activator


def _close_window(app: wx.App, frame: wx.Frame) -> None:
    """Close and destroy *frame*, then settle its deferred deletion."""
    if not frame.IsBeingDeleted():
        frame.Close(force=True)
    if not frame.IsBeingDeleted():
        frame.Destroy()
    _flush_deferred_deletions(app)


def _build_frame(app: wx.App) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Build the main frame, retrying the settle race once or twice."""
    for _attempt in range(3):
        try:
            return app_module.build_main_window(app)
        except LookupError:
            _flush_deferred_deletions(app)
            time.sleep(0.3)
    raise RuntimeError("could not build the main frame after settle retries")


def main() -> int:
    """Run the build/destroy/pump loop and report whether it crashed."""
    app = wx.App()
    crashes = 0
    for i in range(40):
        frame = _build_frame(app)
        app.really_quitting = True
        _close_window(app, frame)
        _flush_deferred_deletions(app)
        gc.collect()
        # Pump longer than the 1 s tick period so a dangling timer fires
        deadline = time.time() + 1.6
        while time.time() < deadline:
            wx.SafeYield()
            time.sleep(0.01)
        crashes += 1
        print(f"iter {i}: survived", flush=True)
    print(f"NO CRASH after {crashes} build/destroy/pump cycles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
