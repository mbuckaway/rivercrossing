# SPDX-License-Identifier: GPL-3.0-only
"""Prove the CI runner has a usable desktop session.

spec.md section 14 asserts both hosted runners have a real
desktop session, so wx windows open without a virtual display.
The whole functional suite rests on that claim, so verify it
explicitly -- open a real frame, draw into it, capture a PNG --
before 23 windows come to depend on it.

Exits non-zero, with the reason, if the session is unusable.
"""

import sys
from pathlib import Path

OUTPUT = Path("ci-gui-probe.png")


def probe() -> str:
    """Open a frame, screenshot it, describe what worked."""
    import wx

    app = wx.App()
    frame = wx.Frame(None, title="RiverCrossing CI probe", size=(480, 320))
    wx.StaticText(frame, label="desktop session ok", pos=(20, 20))
    frame.Show()
    wx.Yield()

    size = frame.GetClientSize()
    bitmap = wx.Bitmap(size.width, size.height)
    memory_dc = wx.MemoryDC(bitmap)
    memory_dc.Blit(0, 0, size.width, size.height, wx.ClientDC(frame), 0, 0)
    del memory_dc

    if not bitmap.SaveFile(str(OUTPUT), wx.BITMAP_TYPE_PNG):
        msg = "frame opened but the screenshot could not be saved"
        raise RuntimeError(msg)

    simulator_ok = hasattr(wx.UIActionSimulator(), "Text")
    frame.Destroy()
    # Deliberately no app.Destroy(): on macOS that blocks and the probe
    # never returns. Letting the interpreter exit tears the app down.
    _ = app

    return (
        f"{wx.version()} | frame shown | {size.width}x{size.height} PNG "
        f"saved to {OUTPUT} | UIActionSimulator usable={simulator_ok}"
    )


def main() -> int:
    """Run the probe and report the outcome."""
    try:
        print(f"desktop session OK: {probe()}")
    except BaseException as exc:  # noqa: BLE001
        # A wxWidgets C++ assertion is not an Exception subclass, and
        # an unusable session is exactly when one fires.
        print(
            f"desktop session UNUSABLE: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
