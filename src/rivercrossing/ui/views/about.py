# SPDX-License-Identifier: GPL-3.0-only
"""``AboutDialog``: ``about_dlg`` (section E), the About box (E8.2.3).

Renders the About box the ``wxID_ABOUT`` route opens: ``version_lbl``
reads the installed package version (``rivercrossing.__version__``),
``about_logo_bmp`` shows the ride's logo -- falling back to the app
icon when no ride is threaded -- and ``gorba_link`` needs no wiring
(``wxHyperlinkCtrl`` opens its own XRC ``<url>`` on click, and
``wxID_CLOSE`` is handled by ``dialogs.run_dialog``'s
``wire_close_button``).

The logo fallback is belt-and-braces because dialogs.xrc declares
``about_logo_bmp`` with no bitmap and its own comment promises the
canvas always shows a logo: ``wx.NullBitmap`` is never acceptable.
The frame's own icon is the first choice, wx's stock information
icon the second, and a drawn suit glyph last -- a chain that can
never yield a null bitmap.
"""

from pathlib import Path
from typing import Any

import wx
import wx.adv

from rivercrossing import __version__
from rivercrossing.ui import ids
from rivercrossing.ui.views._support import find_control

__all__ = ["AboutDialog"]

# The design system's ink/paper tokens (design/README.md), used by the
# drawn placeholder so the fallback matches the app's own palette.
_INK = (29, 32, 33)
_PAPER = (233, 234, 235)


def _resolve_logo_bitmap(logo_path: str | Path | None) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return the About logo: the ride logo file, else the app icon.

    *logo_path* names a real file -> its decoded PNG bitmap. ``None``
    or a path that does not exist falls back to the app icon, so a
    stale saved path can never blank the canvas.
    """
    if logo_path is not None and Path(logo_path).is_file():
        return wx.Bitmap(str(logo_path), wx.BITMAP_TYPE_PNG)
    # logic-coverage-exempt: T-3 -- the missing-file arm of the guard
    # above is unreachable through the route: the store never restores
    # logo_path on load (store's load_engine sets it to None), so the
    # only live input is None or an existing file; a stale path is
    # defensive only.
    return _app_icon_bitmap()


def _app_icon_bitmap() -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return a non-null bitmap for the About logo fallback.

    The top window's own icon first (a frame that carries one --
    ``main.xrc`` sets none today, so ``GetIcon()`` reads ``NullIcon``
    and the branch is inert); the stock information icon second; a
    drawn placeholder last, so the fallback always returns a valid
    bitmap.
    """
    app = wx.GetApp()
    # logic-coverage-exempt: T-3 -- a route handler never runs without
    # a live app and its top window; the None guards only narrow types.
    top = app.GetTopWindow() if app is not None else None
    icon = top.GetIcon() if top is not None else wx.NullIcon
    if icon.IsOk():
        # logic-coverage-exempt: T-3 -- main.xrc sets no frame icon, so
        # IsOk() reads False in every live construction; the frame-icon
        # path runs only on a desktop whose frame carries one.
        return icon.ConvertToBitmap()
    stock = wx.ArtProvider.GetBitmap(wx.ART_INFORMATION, wx.ART_OTHER, wx.Size(64, 64))
    if stock.IsOk():
        return stock
    # logic-coverage-exempt: T-3 -- the drawn placeholder runs only if
    # the stock art provider returns a null bitmap, which neither
    # target platform does for ART_INFORMATION; it is the guaranteed
    # non-null last resort, never exercised in the VM.
    return _drawn_placeholder_bitmap()


def _drawn_placeholder_bitmap() -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Draw the suit glyph into a fresh bitmap (never null)."""
    bitmap = wx.Bitmap(64, 64)
    memory_dc = wx.MemoryDC(bitmap)
    try:
        memory_dc.SetBackground(wx.Brush(wx.Colour(*_INK)))
        memory_dc.Clear()
        memory_dc.SetFont(
            wx.Font(40, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        )
        memory_dc.SetTextForeground(wx.Colour(*_PAPER))
        memory_dc.DrawText("♠", 10, 6)
    finally:
        memory_dc.SelectObject(wx.NullBitmap)
    return bitmap


class AboutDialog:
    """Code-side behaviour for ``about_dlg`` (section E, E8.2.3).

    ``gorba_link`` needs no wiring here: wxHyperlinkCtrl opens its own
    XRC ``<url>`` on click, and ``wxID_CLOSE`` comes from
    ``dialogs.run_dialog``'s ``wire_close_button`` (Escape + click),
    exactly as every other Close-only dialog in this codebase.
    """

    def __init__(
        self,
        dialog: wx.Dialog,
        *,
        logo_path: str | Path | None = None,
    ) -> None:
        """Decorate an already-loaded ``about_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` the app bootstrap already
                loaded from ``dialogs.xrc``.
            logo_path: The ride's logo file (``RideConfig.logo_path``)
                when a ride is threaded; ``None`` (or a path that does
                not exist) falls back to the app icon.
        """
        self.dialog = dialog
        self.version_lbl = find_control(dialog, ids.VERSION_LBL, wx.StaticText)
        # wxHyperlinkCtrl lives under wx.adv (like wx.adv.Sound) -- the
        # XRC class name is wxHyperlinkCtrl, the Python type is
        # wx.adv.HyperlinkCtrl (measured in the VM).
        self.gorba_link = find_control(dialog, ids.GORBA_LINK, wx.adv.HyperlinkCtrl)
        self.about_logo_bmp = find_control(dialog, ids.ABOUT_LOGO_BMP, wx.StaticBitmap)
        self.logo_bitmap = _resolve_logo_bitmap(logo_path)
        self.about_logo_bmp.SetBitmap(self.logo_bitmap)
        self.version_lbl.SetLabel(__version__)
        # The logo can resize the static bitmap; re-layout so the
        # sizer reflows around it before the dialog shows.
        dialog.Layout()
