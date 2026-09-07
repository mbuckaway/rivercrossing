# SPDX-License-Identifier: GPL-3.0-only
"""Custom console gauges: ``RaceClock`` dials and the ``StopLight``.

WS-D's header instrumentation. Three XRC-authored placeholder panels
(``elapsed_clock_panel``/``remaining_clock_panel``/``ride_status_panel``
in ``main.xrc``) receive these controls code-side, because no native
wx control draws an analog dial or a three-circle status lamp --
custom drawing is permitted only where no native control exists
(CODINGSTANDARDS-UX-DESKTOP.md section 1) -- and the classes here
are the whole extent of it on the console.

Drawing follows the measured custom-control recipe this wx build
(4.3.1 / wxWidgets 3.3.3) needs: subclass ``wx.Control``, opt out of
the native background with ``SetBackgroundStyle(wx.BG_STYLE_PAINT)``
so no erase event fights the paint, draw in ``EVT_PAINT`` through
``wx.GCDC(wx.BufferedPaintDC(self))`` for antialiasing, and cache the
static face in a bitmap that is rebuilt only when the client size
changes. The colour scheme follows the ride lifecycle's semantic
colours (RUNNING green / DRAFT+REOPENED amber / FINISHED red) and is
never the sole channel: the status label, banner and clock always
carry the same state in text (UX-DESKTOP section 7).

The GO/STOP glyphs for ``start_btn``/``stop_btn`` (converted to
``wxBitmapButton`` in main.xrc) also live here, as shapes-only SVG
strings rendered through ``wx.BitmapBundle`` so one vector scales for
HiDPI. Measured: ``wx.BitmapBundle.FromSVG`` takes the SVG as UTF-8
*bytes* on this build -- passing ``str`` raises TypeError -- hence the
``.encode("utf-8")`` in :func:`go_bundle`/:func:`stop_bundle`.
"""

import math
from typing import Any

import wx

__all__ = [
    "RaceClock",
    "StopLight",
    "go_bundle",
    "stop_bundle",
]

# The dial's logical size. Fixed like every other canvas metric on
# this console; wx translates logical pixels per-monitor, so the same
# 64 lands at 128 physical pixels on a 2x display.
_DIAL_SIZE = 64
_DIAL_RING_INSET = 4  # outer ring inset from the client edge
_DIAL_TICK_COUNT = 12  # one tick per five minutes of a 60-minute dial
_HAND_INSET = 0.28  # hand length = radius * (1 - inset)

# StopLight geometry, all logical pixels.
_DOT_RADIUS = 6
_DOT_GAP = 7
_DOT_PAD = 4

# The status light's semantic colours (shared with the GO/STOP glyphs
# below so the header speaks one palette).
_GREEN = (30, 142, 62)
_AMBER = (224, 158, 0)
_RED = (194, 20, 46)
_MODE_COLOURS: dict[str, tuple[int, int, int]] = {
    "green": _GREEN,
    "yellow": _AMBER,
    "red": _RED,
}

# Shapes-only GO/STOP glyphs -- a circle plus a play arrow, and a
# rounded square. Deliberately no <text> element: the buttons keep
# their "Start ride"/"Stop ride…" labels for assistive tech and the
# console stays keyboard-first (UX-DESKTOP section 2).
_GO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    f'<circle cx="12" cy="12" r="12" fill="rgb{_GREEN}"/>'
    '<path d="M9.5 6.8v10.4L17.6 12z" fill="#ffffff"/></svg>'
)
_STOP_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    f'<rect x="4" y="4" width="16" height="16" rx="3" fill="rgb{_RED}"/>'
    "</svg>"
)
_GLYPH_SIZE = (20, 20)


def _stop_light_size() -> tuple[int, int]:
    """Return the StopLight's best (width, height) in logical pixels."""
    width = 2 * (_DOT_RADIUS + _DOT_PAD)
    height = 2 * _DOT_PAD + 3 * 2 * _DOT_RADIUS + 2 * _DOT_GAP
    return (width, height)


class RaceClock(wx.Control):  # type: ignore[misc]
    """A round analog dial mapping a 0.0..1.0 fraction to a hand.

    ``# type: ignore[misc]``: wx ships no stubs (pyproject.toml's
    ``ignore_missing_imports`` for ``wx.*``), so ``wx.Control``
    resolves to ``Any`` and mypy refuses to subclass ``Any`` -- the
    same note :class:`CrossingsFeedModel` carries for its own base
    class.

    The static face (ring and ticks) is painted once into a bitmap
    and blitted each ``EVT_PAINT``; only the hand moves, so a 1 s tick
    costs one bitmap blit plus a line. The hand starts at 12 o'clock
    and sweeps a full clockwise circle as the fraction goes 0.0 ->
    1.0. Two instances sit under the elapsed/remaining labels; the
    presenter never tells them which they are -- it only sets the
    fraction each displays (``console.set_clock_fractions``).
    """

    def __init__(self, parent: wx.Window) -> None:
        """Build a DRAFT-positioned dial (hand at 12 o'clock)."""
        super().__init__(parent, size=wx.Size(_DIAL_SIZE, _DIAL_SIZE))
        self.fraction = 0.0
        self._face: wx.Bitmap | None = None
        self._face_size: tuple[int, int] | None = None
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.CacheBestSize(wx.Size(_DIAL_SIZE, _DIAL_SIZE))
        self.Bind(wx.EVT_PAINT, self._on_paint)

    def DoGetBestSize(self) -> wx.Size:
        """Return the dial's fixed logical size."""
        return wx.Size(_DIAL_SIZE, _DIAL_SIZE)

    def set_fraction(self, value: float) -> None:
        """Point the hand at *value* (0.0..1.0) and repaint."""
        self.fraction = value
        self.Refresh()

    # ------------------------------------------------ painting

    def _on_paint(self, _event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Blit the cached face, then draw the hand on top."""
        dc = wx.GCDC(wx.BufferedPaintDC(self))
        width, height = self.GetClientSize()
        dc.DrawBitmap(self._face_bitmap(width, height), 0, 0)
        centre_x = width / 2.0
        centre_y = height / 2.0
        radius = min(width, height) / 2.0 - _DIAL_RING_INSET
        # 12 o'clock is -90 degrees; the hand turns clockwise with the
        # fraction, wx's y-down coordinate system flips the sign.
        angle = self.fraction * 2.0 * math.pi - math.pi / 2.0
        dc.SetPen(wx.Pen(self._ink_colour(), 2))
        hand_length = radius * (1.0 - _HAND_INSET)
        hand_x = centre_x + math.cos(angle) * hand_length
        hand_y = centre_y + math.sin(angle) * hand_length
        # Measured (macOS CI): wx.DC.DrawLine/DrawCircle overloads take
        # integers only -- float args raise TypeError on the Cocoa
        # backend (MSW coerces), so every coordinate is rounded here.
        dc.DrawLine(round(centre_x), round(centre_y), round(hand_x), round(hand_y))
        dc.SetBrush(wx.Brush(self._ink_colour()))
        dc.DrawCircle(round(centre_x), round(centre_y), 2)

    def _face_bitmap(self, width: int, height: int) -> wx.Bitmap:
        """Return the cached face, rebuilt when the size changes."""
        size = (width, height)
        if self._face is not None and self._face_size == size:
            return self._face
        bitmap = wx.Bitmap(width, height)
        memory = wx.GCDC(wx.MemoryDC(bitmap))
        self._draw_face(memory, width, height)
        del memory
        self._face = bitmap
        self._face_size = size
        return bitmap

    def _draw_face(self, dc: Any, width: int, height: int) -> None:  # noqa: ANN401
        """Paint the background disc, ring and ticks into *dc*."""
        background = self.GetParent().GetBackgroundColour()
        dc.SetBackground(wx.Brush(background))
        dc.Clear()
        centre_x = width / 2.0
        centre_y = height / 2.0
        radius = min(width, height) / 2.0 - _DIAL_RING_INSET
        ink = self._ink_colour()
        dc.SetPen(wx.Pen(ink, 1))
        dc.SetBrush(wx.Brush(background))
        dc.DrawCircle(round(centre_x), round(centre_y), round(radius))
        for tick in range(_DIAL_TICK_COUNT):
            angle = tick * 2.0 * math.pi / _DIAL_TICK_COUNT - math.pi / 2.0
            inner_x = centre_x + math.cos(angle) * radius * 0.74
            inner_y = centre_y + math.sin(angle) * radius * 0.74
            outer_x = centre_x + math.cos(angle) * radius * 0.92
            outer_y = centre_y + math.sin(angle) * radius * 0.92
            # Integer overloads only -- float args raise on macOS CI.
            dc.DrawLine(round(inner_x), round(inner_y), round(outer_x), round(outer_y))

    def _ink_colour(self) -> wx.Colour:
        """Return the theme-aware ink (text colour of the platform)."""
        return wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT)


class StopLight(wx.Control):  # type: ignore[misc]
    """Three stacked circles -- green/amber/red -- with one lit.

    ``# type: ignore[misc]``: same wx-ships-no-stubs note as
    :class:`RaceClock`. The lit colour is driven by
    ``console.stop_light_mode`` (RideStatus -> mode) from the view's
    ``set_state``; ``mode`` stays readable for the functional harness.
    The light is display-only: it carries no events, and it never
    carries state by colour alone (the status label beside it always
    spells the state out).
    """

    def __init__(self, parent: wx.Window) -> None:
        """Build the lamp showing the pre-start (DRAFT) amber."""
        super().__init__(parent, size=wx.Size(*_stop_light_size()))
        self.mode = "yellow"  # DRAFT's amber until set_state says otherwise
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.CacheBestSize(wx.Size(*_stop_light_size()))
        self.Bind(wx.EVT_PAINT, self._on_paint)

    def DoGetBestSize(self) -> wx.Size:
        """Return the lamp's fixed logical size."""
        return wx.Size(*_stop_light_size())

    def set_mode(self, mode: str) -> None:
        """Light the circle for *mode* ("green"/"yellow"/"red").

        Raises:
            ValueError: If *mode* is not one of the three lamp colours
                -- a new ride state that forgot to map here must fail
                loudly, not silently dim the lamp.
        """
        if mode not in _MODE_COLOURS:
            raise ValueError(f"unknown stop-light mode {mode!r}")
        self.mode = mode
        self.Refresh()

    # ------------------------------------------------ painting

    def _on_paint(self, _event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Fill the lamp's background and draw the three circles."""
        dc = wx.GCDC(wx.BufferedPaintDC(self))
        width = self.GetClientSize().width
        dc.SetBackground(wx.Brush(self.GetParent().GetBackgroundColour()))
        dc.Clear()
        centre_x = width / 2.0
        for index, mode in enumerate(("green", "yellow", "red")):
            centre_y = _DOT_PAD + _DOT_RADIUS + index * (2 * _DOT_RADIUS + _DOT_GAP)
            if mode == self.mode:
                dc.SetBrush(wx.Brush(wx.Colour(*_MODE_COLOURS[mode])))
                dc.SetPen(wx.Pen(wx.Colour(*_MODE_COLOURS[mode]), 1))
            else:
                dc.SetBrush(wx.TRANSPARENT_BRUSH)
                dc.SetPen(wx.Pen(self._outline_colour(), 1))
            # Integer overloads only -- float args raise on macOS CI.
            dc.DrawCircle(round(centre_x), centre_y, _DOT_RADIUS)

    def _outline_colour(self) -> wx.Colour:
        """Return the unlit circles' outline (theme-aware grey)."""
        return wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT)


def go_bundle() -> wx.BitmapBundle:
    """Return the GO glyph as a scalable bitmap bundle (WS-D)."""
    return wx.BitmapBundle.FromSVG(_GO_SVG.encode("utf-8"), _GLYPH_SIZE)


def stop_bundle() -> wx.BitmapBundle:
    """Return the STOP glyph as a scalable bitmap bundle (WS-D)."""
    return wx.BitmapBundle.FromSVG(_STOP_SVG.encode("utf-8"), _GLYPH_SIZE)
