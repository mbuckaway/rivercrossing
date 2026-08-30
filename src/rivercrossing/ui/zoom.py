# SPDX-License-Identifier: GPL-3.0-only
"""Text zoom: the View > Zoom ladder (R-04, spec §13, E8.1.4).

The seven View > Zoom radios (``mi_zoom_90`` ... ``mi_zoom_150``) scale
every window's fonts by ``percent/100`` -- the in-app text zoom R-04
owns. Mirrors ``theme.py``'s split: the id->percent mapping and the
scaling math are wx-free, and everything that touches a real font
calls :func:`~rivercrossing.ui.require_wx` at the point wx is first
needed, never at import time. The ladder itself is
``ui.presenters.settings.ZOOM_LADDER`` -- the single source shared
with the XRC ``zoom_choice`` (settings.xrc) -- never duplicated here.

Font scaling is robust by construction: each window's base font is
captured ONCE (stored as ``window._zoom_base_font`` on first apply)
and every re-apply scales from that base, so a different percent never
compounds. Explicitly-fonted controls (``plate_input``'s XRC
``<relativesize>1.5</relativesize>``) scale from their own captured
base, preserving their relative size.
"""

from typing import Any

from rivercrossing.ui import ids, require_wx
from rivercrossing.ui.presenters.settings import ZOOM_LADDER

__all__ = [
    "ZOOM_MENU_ITEM_IDS",
    "UnknownZoomMenuItemError",
    "ZoomController",
    "apply_to",
    "menu_item_id_for",
    "percent_for_menu_id",
    "scaled_point_size",
    "set_percent",
]

# mi_zoom_90 ... mi_zoom_150, keyed by percent (main.xrc's declared
# order is ZOOM_LADDER's; the id tuple below keeps that order).
_ITEM_ID_BY_PERCENT: dict[int, str] = {
    90: ids.MI_ZOOM_90,
    100: ids.MI_ZOOM_100,
    110: ids.MI_ZOOM_110,
    120: ids.MI_ZOOM_120,
    130: ids.MI_ZOOM_130,
    140: ids.MI_ZOOM_140,
    150: ids.MI_ZOOM_150,
}
_PERCENT_BY_ITEM_ID: dict[str, int] = {
    item_id: percent for percent, item_id in _ITEM_ID_BY_PERCENT.items()
}
ZOOM_MENU_ITEM_IDS: tuple[str, ...] = tuple(
    _ITEM_ID_BY_PERCENT[percent] for percent in ZOOM_LADDER
)


class UnknownZoomMenuItemError(LookupError):
    """Raised when a menu item id maps to no zoom percent."""


def percent_for_menu_id(item_id: str) -> int:
    """Return the zoom percent the radio *item_id* selects.

    Args:
        item_id: One of :data:`ZOOM_MENU_ITEM_IDS`.

    Returns:
        The matching percent (90..150, step 10).

    Raises:
        UnknownZoomMenuItemError: If *item_id* names no zoom radio.
    """
    try:
        return _PERCENT_BY_ITEM_ID[item_id]
    except KeyError as exc:
        raise UnknownZoomMenuItemError(f"no zoom mapping for menu item id {item_id!r}") from exc


def menu_item_id_for(percent: int) -> str:
    """Return the zoom radio's XRC name for *percent*.

    The reverse of :func:`percent_for_menu_id`: the bootstrap's
    restored-zoom radio check and the settings-dialog mirror need the
    id for a percent. A percent outside :data:`ZOOM_LADDER` raises
    ``KeyError`` -- the caller always passes a clamped value.
    """
    return _ITEM_ID_BY_PERCENT[percent]


def scaled_point_size(base_point_size: int, percent: int) -> int:
    """Return *base_point_size* scaled to *percent* (E8.1.4).

    ``round(base * percent / 100)``, floored at 1 so a degenerate 0%
    can never produce a zero-size font.
    """
    return max(1, round(base_point_size * percent / 100))


def _scaled_font(base_font: Any, percent: int) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return *base_font* at the point size scaled to *percent*."""
    wx = require_wx()
    return wx.Font(
        scaled_point_size(base_font.GetPointSize(), percent),
        base_font.GetFamily(),
        base_font.GetStyle(),
        base_font.GetWeight(),
        base_font.GetUnderlined(),
        base_font.GetFaceName(),
    )


def _apply_fonts(window: Any, percent: int) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Scale *window*'s font and every descendant's to *percent*.

    Captures *window*'s base font once (``window._zoom_base_font``)
    on first application; every later apply scales from that captured
    base, so re-zooming never compounds. Recurses ``GetChildren()`` so
    explicitly-fonted controls scale from their own base.
    """
    base_font = getattr(window, "_zoom_base_font", None)
    if base_font is None:
        base_font = window.GetFont()
        window._zoom_base_font = base_font  # noqa: SLF001 -- the E8.1.4 capture attribute, deliberately a plain window attribute
    window.SetFont(_scaled_font(base_font, percent))
    for child in window.GetChildren():
        _apply_fonts(child, percent)


class ZoomController:
    """Owns the selected zoom percent and applies it (E8.1.4).

    Constructed once per app bootstrap and driven through the module
    convenience (:func:`set_percent`/:func:`apply_to`), mirroring
    ``sound.SoundPlayer``'s relationship to ``sound.set_muted``. Starts
    at 100% -- the checked menu default.
    """

    def __init__(self, percent: int = 100) -> None:
        """Store the starting *percent* without applying it."""
        self._percent = percent

    @property
    def percent(self) -> int:
        """Return the currently selected percent."""
        return self._percent

    def apply(self, percent: int) -> None:
        """Set *percent* and scale every open top-level window to it."""
        self._percent = percent
        wx = require_wx()
        for window in wx.GetTopLevelWindows():
            _apply_fonts(window, percent)

    def apply_to(self, window: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Scale *window* and its descendants at the current percent."""
        _apply_fonts(window, self._percent)


# The one default controller the app's convenience functions drive;
# the View-menu radios and the settings choice flip it (E8.1.4).
_default_controller = ZoomController()


def set_percent(percent: int) -> None:
    """Set the current zoom and scale every open window to *percent*."""
    _default_controller.apply(percent)


def apply_to(window: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Scale *window* and its descendants at the current zoom."""
    _default_controller.apply_to(window)
