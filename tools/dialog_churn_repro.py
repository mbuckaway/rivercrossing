# SPDX-License-Identifier: GPL-3.0-only
"""Churn probe: build/destroy ride_setup_dlg repeatedly in one process.

On an address-reuse failure (a recycled C++ address whose SIP wrapper
cache still maps to a destroyed control's class), dump who holds the
stale wrapper via gc.get_referrers, to identify the retainer behind
the functional suite's residual LookupError class.
"""

import faulthandler
import gc
import sys

faulthandler.enable()

import wx  # noqa: E402

sys.path.insert(0, "tests/functional")
import harness  # noqa: E402

from rivercrossing.roster import Roster  # noqa: E402
from rivercrossing.ui import ids  # noqa: E402
from rivercrossing.ui.views.ride_setup import RideSetup  # noqa: E402


def main() -> int:
    """Churn the dialog and report the first stale-wrapper retainer."""
    app = wx.App()
    resource = harness.load_xrc_resources()
    for i in range(80):
        dialog = harness.load_window_verified(resource, ids.RIDE_SETUP_DLG, frame=False)
        try:
            try:
                RideSetup(dialog, roster=Roster())
                dialog.Show()
                harness.pump()
                harness.click(dialog, ids.CAP_CHK)
                other = harness.load_window_verified(resource, ids.RIDER_EDITOR_DLG, frame=False)
                harness.run_modal(other, dismiss_with=wx.ID_CANCEL)
                harness.close_window(other)
            except LookupError:
                stale = wx.Window.FindWindowByName("lap_km_spin", dialog)
                kind = type(stale).__name__
                print(f"iter {i}: STALE lap_km_spin wrapper type={kind}", flush=True)
                for referrer in gc.get_referrers(stale):
                    kind = type(referrer).__name__
                    if kind in ("list", "dict", "set", "tuple", "function", "module", "frame"):
                        continue
                    print(f"  referrer: {kind} -> {repr(referrer)[:140]}", flush=True)
                return 1
        finally:
            app.really_quitting = True
            harness.close_window(dialog)
        gc.collect()
        if i % 10 == 0:
            print(f"iter {i}: clean", flush=True)
    print("NO STALE WRAPPER after 80 full-shape churn cycles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
