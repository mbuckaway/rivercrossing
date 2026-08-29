"""Minimal repro for the functional-suite segfaults (dangling tick timer).

Mirrors what the functional tests do: build the app's main frame (which
starts the 1 s tick wx.Timer in main_frame.wire_console), destroy the
frame (test teardown), then pump via wx.SafeYield() for longer than the
timer period so the native timer fires against the freed frame.

Hypothesis: destroying the frame does not stop _tick_timer, so the
next SafeYield dispatches wxTimerImpl::SendEvent -> SafelyProcessEvent
on freed memory (the measured crash frame).
"""

import faulthandler
import sys
import time

faulthandler.enable()

import wx  # noqa: E402

sys.path.insert(0, "tests/functional")
import harness  # noqa: E402
from rivercrossing.ui import app as app_module  # noqa: E402


def main() -> int:
    """Run the build/destroy/pump loop and report whether it crashed."""
    app = wx.App()
    crashes = 0
    for i in range(40):
        frame = app_module.build_main_window(app)
        app.really_quitting = True
        harness.close_window(frame)
        # pump longer than the 1 s tick period so the timer fires
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
