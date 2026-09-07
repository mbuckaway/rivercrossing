# SPDX-License-Identifier: GPL-3.0-only
"""``AboutDialog``: ``about_dlg`` (section E), the About box (E8.2.3).

Renders the About box the ``wxID_ABOUT`` route opens: ``version_lbl``
reads the product name plus the installed package version
(``RiverCrossing <rivercrossing.__version__>``), ``about_logo_bmp``
shows the ride's logo -- falling back to a non-null logo when no
ride is threaded -- and ``gorba_link`` needs no wiring
(``wxHyperlinkCtrl`` opens its own XRC ``<url>`` on click, and
``wxID_CLOSE`` is handled by ``dialogs.run_dialog``'s
``wire_close_button``).

The logo fallback is belt-and-braces because dialogs.xrc declares
``about_logo_bmp`` with no bitmap and its own comment promises the
canvas always shows a logo: ``wx.NullBitmap`` is never acceptable.
The ride logo is the first choice; without one the chain is the
frame's own icon, the embedded RiverCrossing logo
(:data:`EMBEDDED_LOGO_SVG`, rasterised through
``wx.BitmapBundle.FromSVG`` -- ux-polish), wx's stock information
icon, and a drawn suit glyph last, so the fallback can never yield a
null bitmap.

``about_dlg`` is fixed-size (ux-polish): its short, fixed copy must
never wrap from a user resize, so ``dialogs.xrc`` drops
``wxRESIZE_BORDER`` and this module pins :data:`ABOUT_SIZE`.
"""

from pathlib import Path
from typing import Any

import wx
import wx.adv

from rivercrossing import __version__
from rivercrossing.ui import ids
from rivercrossing.ui.views._support import find_control

__all__ = [
    "ABOUT_LOGO_SIZE",
    "ABOUT_SIZE",
    "EMBEDDED_LOGO_SVG",
    "AboutDialog",
]

# The design system's ink/paper tokens (design/README.md), used by the
# drawn placeholder so the fallback matches the app's own palette.
_INK = (29, 32, 33)
_PAPER = (233, 234, 235)

# Pinned About-box size (ux-polish): about_dlg carries no
# wxRESIZE_BORDER and the dialog is fixed here -- SetSize for the
# current size, min/max hints below so nothing can resize it -- so
# the fixed copy can never re-wrap. Generous enough that the longest
# line ("Timing & poker-hand scoring for poker-run rides.") holds at
# the in-app 150% zoom; deliberately not tuned further.
ABOUT_SIZE = wx.Size(500, 420)

# The About logo's nominal render size, wx units at 100% DPI;
# BitmapBundle scales it for the target control's own DPI.
ABOUT_LOGO_SIZE = wx.Size(64, 64)

# Small RiverCrossing logo (installers/branding/svg/icon.svg's motif:
# a playing card and stopwatch on the felt-green plate -- P8-D6),
# authored for the SVG subset wx.BitmapBundle decodes on both target
# platforms: flat hex fills, simple strokes, no text, no transforms.
# Markup is split across lines like the icon.svg source; the XML
# parser treats the whitespace as token separators.
EMBEDDED_LOGO_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
  <rect x="5" y="5" width="54" height="54" rx="12" fill="#1E5C3F"/>
  <rect x="8.5" y="8.5" width="47" height="47" rx="10"
        fill="none" stroke="#2E7A54" stroke-width="1"/>
  <rect x="12" y="12" width="30" height="40" rx="5"
        fill="#FBFBF4" stroke="#1F2937" stroke-width="1.5"/>
  <path fill="#111827"
        d="M27 24 C33 28 35 32 35 36 C35 40 31 42 28 40 C29 44 31 47 33 49
           L21 49 C23 47 25 44 26 40 C23 42 19 40 19 36 C19 32 21 28 27 24 Z"/>
  <circle cx="47" cy="46" r="12" fill="#E8EAED" stroke="#1F2937" stroke-width="1.5"/>
  <circle cx="47" cy="46" r="9" fill="#FFFFFF" stroke="#9CA3AF" stroke-width="1"/>
  <rect x="44" y="25" width="6" height="8" fill="#E8EAED" stroke="#1F2937" stroke-width="1"/>
  <path d="M47 46 L53 40" stroke="#F59E0B" stroke-width="2.5"/>
  <circle cx="47" cy="46" r="2" fill="#1F2937"/>
</svg>
"""


def _resolve_logo_bitmap(logo_path: str | Path | None, window: Any) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return the About logo: the ride file or the fallback chain.

    *logo_path* names a real file -> its decoded PNG bitmap. ``None``
    or a path that does not exist falls back to a guaranteed non-null
    bitmap (:func:`_fallback_logo_bitmap`), so a stale saved path can
    never blank the canvas. *window* is the logo's target control: the
    embedded-SVG arm rasterises at that window's DPI.

    Args:
        logo_path: The ride's logo file, or ``None``.
        window: The ``about_logo_bmp`` control the bitmap lands on.

    Returns:
        A valid ``wx.Bitmap``.
    """
    if logo_path is not None and Path(logo_path).is_file():
        bitmap = wx.Bitmap(str(logo_path), wx.BITMAP_TYPE_PNG)
        if bitmap.IsOk():
            return bitmap
        # The file exists but wx cannot decode it: fall through to the
        # fallback chain rather than blanking the canvas (measured in
        # the VM -- wx's PNG decoder is stricter than PIL's).
    # logic-coverage-exempt: T-3 -- the missing-file/undecodable-file
    # arms of the guard above are unreachable through the route: the
    # store never restores logo_path on load (store's load_engine sets
    # it to None), so the only live input is None or an existing,
    # decodable file; the arms are defensive only.
    return _fallback_logo_bitmap(window)


def _fallback_logo_bitmap(window: Any) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return a non-null bitmap for the About logo without a ride logo.

    The top window's own icon first (a frame that carries one --
    ``main.xrc`` sets none today, so ``GetIcon()`` reads ``NullIcon``
    and the branch is inert); the embedded RiverCrossing logo second
    (ux-polish: it must come before the stock icon so the About box
    shows a *real* logo on every platform); the stock information
    icon third; a drawn placeholder last, so the fallback always
    returns a valid bitmap.

    Args:
        window: The logo's target control; the embedded-SVG arm
            renders at this window's DPI (``GetBitmapFor``).
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
    embedded = _embedded_logo_bitmap(window)
    if embedded.IsOk():
        return embedded
    stock = wx.ArtProvider.GetBitmap(wx.ART_INFORMATION, wx.ART_OTHER, wx.Size(64, 64))
    if stock.IsOk():
        return stock
    # logic-coverage-exempt: T-3 -- the drawn placeholder runs only if
    # both the embedded logo and the stock art provider return a null
    # bitmap, which neither target platform produces for a well-formed
    # SVG or ART_INFORMATION; it is the guaranteed non-null last
    # resort, never exercised in the VM.
    return _drawn_placeholder_bitmap()


def _embedded_logo_bitmap(window: Any) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Rasterise :data:`EMBEDDED_LOGO_SVG` for *window*'s DPI."""
    bundle = wx.BitmapBundle.FromSVG(EMBEDDED_LOGO_SVG.encode("utf-8"), ABOUT_LOGO_SIZE)
    return bundle.GetBitmapFor(window)


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

        Pins the dialog to :data:`ABOUT_SIZE` (its XRC style drops
        ``wxRESIZE_BORDER``), then renders version, logo and layout.

        Args:
            dialog: The ``wx.Dialog`` the app bootstrap already
                loaded from ``dialogs.xrc``.
            logo_path: The ride's logo file (``RideConfig.logo_path``)
                when a ride is threaded; ``None`` (or a path that does
                not exist) falls back to the non-null logo chain.
        """
        self.dialog = dialog
        # Fixed ABOUT_SIZE (ux-polish): SetSize pins the current size
        # and the min/max hints forbid any later resize, so the fixed
        # copy cannot re-wrap. wxPython 4.3.1 wraps no SetFixedSize
        # (measured: absent from the whole module), so the wxWidgets
        # fixed-size effect is applied directly -- SetMinSize +
        # SetMaxSize are exactly SetFixedSize's documented hints pair.
        dialog.SetSize(ABOUT_SIZE)
        dialog.SetMinSize(ABOUT_SIZE)
        dialog.SetMaxSize(ABOUT_SIZE)
        self.version_lbl = find_control(dialog, ids.VERSION_LBL, wx.StaticText)
        # wxHyperlinkCtrl lives under wx.adv (like wx.adv.Sound) -- the
        # XRC class name is wxHyperlinkCtrl, the Python type is
        # wx.adv.HyperlinkCtrl (measured in the VM).
        self.gorba_link = find_control(dialog, ids.GORBA_LINK, wx.adv.HyperlinkCtrl)
        self.about_logo_bmp = find_control(dialog, ids.ABOUT_LOGO_BMP, wx.StaticBitmap)
        # The target control rides along so the embedded-SVG arm of the
        # fallback can render at the control's own DPI.
        self.logo_bitmap = _resolve_logo_bitmap(logo_path, self.about_logo_bmp)
        self.about_logo_bmp.SetBitmap(self.logo_bitmap)
        self.version_lbl.SetLabel(f"RiverCrossing {__version__}")
        # The logo can resize the static bitmap; re-layout so the
        # sizer reflows around it before the dialog shows.
        dialog.Layout()
