# SPDX-License-Identifier: GPL-3.0-only
"""Diagnose the resume_library scenario hang (diagnostic tool).

Runs the exact scenario flow in-process and dumps the stack after 20 s
if it has not returned, so the hang point is visible.
"""

import faulthandler
import sys
import time

faulthandler.enable()
faulthandler.dump_traceback_later(20, exit=True)

import wx  # noqa: E402

sys.path.insert(0, "tests/functional")
import console_subprocess_scenarios as scenarios  # noqa: E402
import harness  # noqa: E402

from rivercrossing.store import Store  # noqa: E402
from rivercrossing.ui import app as app_module  # noqa: E402
from rivercrossing.ui import ids  # noqa: E402


def main() -> int:
    """Run the scenario and report its result."""
    _app = wx.App()
    db_path = scenarios._resume_db_path("rc-diag-")  # noqa: SLF001 -- diagnostic
    scenarios._create_resumed_ride(  # noqa: SLF001 -- diagnostic
        db_path,
        scenarios._ResumeRideSpec(quit_cleanly=True),  # noqa: SLF001 -- diagnostic
    )
    store = Store.open(db_path)
    found: dict[str, object] = {}

    def _click_library() -> None:
        dialog = wx.Window.FindWindowByName(ids.RESUME_DLG)
        found["resume_shown"] = dialog is not None and dialog.IsShown()
        print(f"click_library: resume_shown={found['resume_shown']}", flush=True)
        if dialog is not None:
            harness.click(dialog, ids.LIBRARY_BTN)
        print("click_library: after click", flush=True)

    def _probe_and_dismiss_library() -> None:
        library = wx.Window.FindWindowByName(ids.RIDE_LIBRARY_DLG)
        found["library_shown"] = library is not None and library.IsShown()
        print(f"probe: library_shown={found['library_shown']}", flush=True)
        if library is not None:
            # Dismiss via EndModal directly -- harness.click pumps
            # (SafeYield) inside the modal loop, which re-enters it and
            # hangs (measured).
            wx.CallAfter(library.EndModal, wx.ID_CLOSE)
        print("probe: EndModal queued", flush=True)

    wx.CallAfter(_click_library)
    frame = app_module.build_main_window(wx.GetApp(), store=store)
    frame.Show()
    frame.Layout()
    wx.CallAfter(_probe_and_dismiss_library)
    wx.CallLater(3000, lambda: scenarios._close_without_prompt(frame))  # noqa: SLF001 -- diagnostic
    start = time.time()
    wx.GetApp().MainLoop()
    print(f"MainLoop returned after {time.time() - start:.1f}s", flush=True)
    try:
        return {
            "resume_dlg_shown": found.get("resume_shown", False),
            "library_shown": found.get("library_shown", False),
        }
    finally:
        store.close()


if __name__ == "__main__":
    print(main(), flush=True)
