# SPDX-License-Identifier: GPL-3.0-only
"""``AboutDialog``: ``about_dlg`` (section E), the About box (E8.2.3).

Renders the About box the ``wxID_ABOUT`` route opens: ``version_lbl``
reads the product name plus the installed package version
(``RiverCrossing <rivercrossing.__version__>``) and ``gorba_link``
needs no wiring (``wxHyperlinkCtrl`` opens its own XRC ``<url>`` on
click, and ``wxID_CLOSE`` is handled by ``dialogs.run_dialog``'s
``wire_close_button``).

``about_dlg`` is fixed-size (ux-polish): its short, fixed copy must
never wrap from a user resize, so ``dialogs.xrc`` drops
``wxRESIZE_BORDER`` and this module pins :data:`ABOUT_SIZE`.
"""

import wx
import wx.adv

from rivercrossing import __version__
from rivercrossing.ui import ids
from rivercrossing.ui.views._support import find_control

__all__ = [
    "ABOUT_SIZE",
    "AboutDialog",
]

# Pinned About-box size (ux-polish): about_dlg carries no
# wxRESIZE_BORDER and the dialog is fixed here -- SetSize for the
# current size, min/max hints below so nothing can resize it -- so
# the fixed copy can never re-wrap. Generous enough that the longest
# line ("Timing & poker-hand scoring for poker-run rides.") holds at
# the in-app 150% zoom; deliberately not tuned further.
ABOUT_SIZE = wx.Size(500, 420)


class AboutDialog:
    """Code-side behaviour for ``about_dlg`` (section E, E8.2.3).

    ``gorba_link`` needs no wiring here: wxHyperlinkCtrl opens its own
    XRC ``<url>`` on click, and ``wxID_CLOSE`` comes from
    ``dialogs.run_dialog``'s ``wire_close_button`` (Escape + click),
    exactly as every other Close-only dialog in this codebase.
    """

    def __init__(self, dialog: wx.Dialog) -> None:
        """Decorate an already-loaded ``about_dlg`` window.

        Pins the dialog to :data:`ABOUT_SIZE` (its XRC style drops
        ``wxRESIZE_BORDER``), then renders the version and layout.

        Args:
            dialog: The ``wx.Dialog`` the app bootstrap already
                loaded from ``dialogs.xrc``.
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
        self.version_lbl.SetLabel(f"RiverCrossing {__version__}")
        dialog.Layout()
