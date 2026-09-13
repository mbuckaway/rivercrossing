# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the cross-platform screen fit (Phase 6, part 1).

``_support.fit_frame_to_screen`` is the one place the console frame is
made to fit the display it will actually appear on: the window's size
is clamped to the work area minus a 16 px margin, its position is
pulled back inside that area, and the *effective* minimum size is the
requested floor bounded by the same area -- so a floor taller than the
screen never forces a window larger than the screen.

Cross-platform by construction, no OS branch: the work area comes from
``wx.Display(idx).GetClientArea()``, which excludes the macOS menu
bar/Dock and the Windows taskbar on both hosts. Measured on wxPython
4.3.1 / wxWidgets 3.3.3: ``wx.Display``, ``wx.Display.GetFromWindow``,
``wx.Display.GetClientArea`` and ``wx.EVT_DISPLAY_CHANGED`` all exist;
``wx.Display.GetWorkArea`` does not.

``wx.Display(...)`` raises ``PyNoAppError`` without a ``wx.App``, so
these tests swap ``wx.Display`` for a table-driven double -- the
display read is the helper's GUI I/O boundary (T-10), the maths
around it is the logic under test. The frame is a recording double
for the same reason (a real ``wx.Frame`` needs a desktop), and each
test makes exactly one call on the SUT (T-8).

The second half pins ``MainFrame``'s own two uses -- the size and
geometry restore the constructor runs, and the ``EVT_DISPLAY_CHANGED``
re-fit -- through the same stand-in shape
``test_main_frame_ride_header.py`` uses: ``object.__new__`` plus
recording doubles, so no window is ever created.
"""

import inspect
from unittest.mock import patch

import pytest
import wx
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui.views import main_frame
from rivercrossing.ui.views._support import FRAME_SCREEN_MARGIN, fit_frame_to_screen

# A work area roomy enough for the console's own floor (1100x780), with
# the menu bar carved out of the top the way GetClientArea reports it.
_ROOMY_DISPLAY = (0, 25, 1920, 1080)

# The 1366x768 floor CODINGSTANDARDS-UX-DESKTOP section 6 names: the
# console floor no longer fits, so size and floor both shrink.
_SMALL_DISPLAY = (0, 25, 1366, 768)

# Smaller than the console's own minimum in both dimensions.
_TINY_DISPLAY = (0, 0, 900, 500)


class _DisplayDouble:
    """One ``wx.Display`` instance double: a scripted client area."""

    def __init__(self, rect: tuple[int, int, int, int]) -> None:
        """Report *rect* (x, y, width, height) from GetClientArea."""
        self._rect = wx.Rect(*rect)

    def GetClientArea(self) -> wx.Rect:  # noqa: N802 -- wx API name the SUT calls
        """Report the scripted work area."""
        return wx.Rect(self._rect)


class _DisplayTable:
    """The ``wx.Display`` facet double: static lookup plus constructor.

    The SUT uses exactly two members -- ``wx.Display.GetFromWindow``
    and ``wx.Display(index)`` -- so the double answers both: an
    instance's bound ``GetFromWindow`` and ``__call__``.
    """

    def __init__(self, rect: tuple[int, int, int, int], index: int = 0) -> None:
        """Report *index* for any window; hand out *rect*'s area."""
        self._rect = rect
        self._index = index
        self.requested: list[int] = []

    def GetFromWindow(self, _window: object) -> int:  # noqa: N802 -- wx API name
        """Report the scripted display index for the window."""
        return self._index

    def __call__(self, index: int) -> _DisplayDouble:
        """Record the index and return that display's double."""
        self.requested.append(index)
        return _DisplayDouble(self._rect)


class _FrameDouble:
    """A ``wx.Frame`` double recording every call the SUT makes."""

    def __init__(
        self,
        size: tuple[int, int] = (400, 300),
        position: tuple[int, int] = (0, 0),
    ) -> None:
        """Start at *size*/*position*, with an empty call log."""
        self.size = wx.Size(*size)
        self.position = wx.Point(*position)
        self.calls: list[str] = []
        self.min_size: wx.Size | None = None

    def GetSize(self) -> wx.Size:  # noqa: N802 -- wx API name the SUT calls
        """Record the read and report the current size."""
        self.calls.append("GetSize")
        return wx.Size(self.size)

    def GetPosition(self) -> wx.Point:  # noqa: N802 -- wx API name
        """Record the read and report the current position."""
        self.calls.append("GetPosition")
        return wx.Point(self.position)

    def SetMinSize(self, size: wx.Size) -> None:  # noqa: N802 -- wx API name
        """Record the floor the SUT applied."""
        self.calls.append("SetMinSize")
        self.min_size = wx.Size(size)

    def SetSize(self, size: wx.Size) -> None:  # noqa: N802 -- wx API name
        """Record the size the SUT applied."""
        self.calls.append("SetSize")
        self.size = wx.Size(size)

    def SetPosition(  # noqa: N802 -- wx API name the SUT calls
        self, position: wx.Point
    ) -> None:
        """Record the position the SUT applied."""
        self.calls.append("SetPosition")
        self.position = wx.Point(position)


class _RecordingEvent:
    """A wx event double recording the handler's Skip."""

    def __init__(self) -> None:
        """Start unsent, as wx hands an un-Skipped event."""
        self.skipped = False

    def Skip(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record that the handler let the event continue."""
        self.skipped = True


def _stub_display(
    monkeypatch: pytest.MonkeyPatch,
    rect: tuple[int, int, int, int],
    index: int = 0,
) -> _DisplayTable:
    """Point ``wx.Display`` at a fresh table double for one test."""
    display = _DisplayTable(rect, index)
    monkeypatch.setattr(wx, "Display", display)
    return display


def _bare_view(frame: _FrameDouble) -> main_frame.MainFrame:
    """Return a ``MainFrame`` over *frame* alone.

    ``__init__`` resolves every frozen name and builds real wx windows,
    which needs a desktop; the two screen-fit seams read and write only
    ``self.frame``, so the instance is made without it -- the same
    stand-in ``test_main_frame_ride_header.py`` uses.
    """
    view = object.__new__(main_frame.MainFrame)
    view.frame = frame
    return view


# ------------------------------------------------------ the margin


def test_frame_screen_margin_is_a_16_pixel_border() -> None:
    """Part 1's own number: the 16 px work-area border."""
    assert FRAME_SCREEN_MARGIN == 16


# --------------------------------------------------- size and floor


def test_fit_frame_to_screen_given_a_roomy_display_keeps_the_frame_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frame that already fits is never resized."""
    _stub_display(monkeypatch, _ROOMY_DISPLAY)
    frame = _FrameDouble(size=(1100, 780), position=(100, 100))

    fit_frame_to_screen(frame, (1100, 780))

    assert (frame.size.width, frame.size.height) == (1100, 780)


def test_fit_frame_to_screen_given_a_roomy_display_applies_the_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The min size is the caller's floor when the display has room."""
    _stub_display(monkeypatch, _ROOMY_DISPLAY)
    frame = _FrameDouble(size=(1100, 780))

    fit_frame_to_screen(frame, (1100, 780))

    assert (frame.min_size.width, frame.min_size.height) == (1100, 780)


def test_fit_frame_to_screen_given_a_smaller_display_clamps_the_frame_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-4 above the floor: 1366x768 less the margin wins."""
    _stub_display(monkeypatch, _SMALL_DISPLAY)
    frame = _FrameDouble(size=(1600, 900))

    fit_frame_to_screen(frame, (1100, 780))

    assert (frame.size.width, frame.size.height) == (1366 - 32, 768 - 32)


def test_fit_frame_to_screen_given_a_display_below_the_floor_shrinks_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-4 below the floor: the minimum cannot exceed the area."""
    _stub_display(monkeypatch, _TINY_DISPLAY)
    frame = _FrameDouble(size=(1100, 780))

    fit_frame_to_screen(frame, (1100, 780))

    assert (frame.min_size.width, frame.min_size.height) == (900 - 32, 500 - 32)


@pytest.mark.parametrize(
    ("rect", "expected"),
    [
        ((0, 25, 1366, 768), (1334, 736)),  # T-4: the field-laptop floor
        ((0, 25, 1367, 769), (1335, 737)),  # T-4: floor + 1
        ((0, 25, 1365, 767), (1333, 735)),  # T-4: floor - 1
        ((0, 0, 0, 0), (0, 0)),  # T-4: the degenerate empty work area
    ],
    ids=["floor", "floor_plus_one", "floor_minus_one", "empty"],
)
def test_fit_frame_to_screen_given_any_display_clamps_to_its_work_area(
    monkeypatch: pytest.MonkeyPatch,
    rect: tuple[int, int, int, int],
    expected: tuple[int, int],
) -> None:
    """The clamped size is always the work area minus both margins."""
    _stub_display(monkeypatch, rect)
    frame = _FrameDouble(size=(4000, 4000))

    fit_frame_to_screen(frame, (1100, 780))

    assert (frame.size.width, frame.size.height) == expected


# ------------------------------------------------------------ position


def test_fit_frame_to_screen_given_an_inside_position_leaves_it_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frame already in the work area is not moved."""
    _stub_display(monkeypatch, _ROOMY_DISPLAY)
    frame = _FrameDouble(size=(1100, 780), position=(100, 100))

    fit_frame_to_screen(frame, (1100, 780))

    assert (frame.position.x, frame.position.y) == (100, 100)


def test_fit_frame_to_screen_given_an_off_screen_position_pulls_it_inside(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A window restored past the bottom-right is pulled back."""
    _stub_display(monkeypatch, _ROOMY_DISPLAY)
    frame = _FrameDouble(size=(1100, 780), position=(5000, 4000))

    fit_frame_to_screen(frame, (1100, 780))

    assert (frame.position.x, frame.position.y) == (1920 - 1100, 25 + 1080 - 780)


def test_fit_frame_to_screen_given_a_position_above_it_pulls_it_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A negative position -- a disconnected display's coordinates."""
    _stub_display(monkeypatch, _ROOMY_DISPLAY)
    frame = _FrameDouble(size=(1100, 780), position=(-300, -200))

    fit_frame_to_screen(frame, (1100, 780))

    assert (frame.position.x, frame.position.y) == (0, 25)


# ------------------------------------------------------------- display


def test_fit_frame_to_screen_given_no_display_falls_back_to_the_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``wx.NOT_FOUND`` (a window on no display) falls back to 0."""
    display = _stub_display(monkeypatch, _ROOMY_DISPLAY, index=wx.NOT_FOUND)
    frame = _FrameDouble(size=(1100, 780), position=(100, 100))

    fit_frame_to_screen(frame, (1100, 780))

    assert display.requested == [0]


def test_fit_frame_to_screen_given_a_second_display_uses_its_area(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The frame is fitted to the display it is actually on."""
    display = _stub_display(monkeypatch, _SMALL_DISPLAY, index=1)
    frame = _FrameDouble(size=(4000, 4000), position=(100, 100))

    fit_frame_to_screen(frame, (1100, 780))

    assert (display.requested, frame.size.width) == ([1], 1366 - 32)


def test_fit_frame_to_screen_applies_the_floor_then_size_then_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact order the constructor's Layout() depends on."""
    _stub_display(monkeypatch, _ROOMY_DISPLAY)
    frame = _FrameDouble(size=(1100, 780), position=(100, 100))

    fit_frame_to_screen(frame, (1100, 780))

    assert frame.calls == ["SetMinSize", "GetSize", "SetSize", "GetPosition", "SetPosition"]


# ------------------------------------------------------------ property


@given(
    size=st.tuples(
        st.integers(min_value=200, max_value=4000),
        st.integers(min_value=200, max_value=4000),
    ),
    position=st.tuples(
        st.integers(min_value=-5000, max_value=5000),
        st.integers(min_value=-5000, max_value=5000),
    ),
)
def test_fit_frame_to_screen_given_any_request_keeps_it_in_the_area(
    size: tuple[int, int], position: tuple[int, int]
) -> None:
    """T-7 invariant: the result is always visible and never grows.

    Every generated frame comes back fully inside the work area and no
    larger than the request, whatever size and position it arrived
    with -- the whole point of the fit.
    """
    # A context manager, not the monkeypatch fixture: Hypothesis does
    # not reset a function-scoped fixture between generated inputs.
    with patch.object(wx, "Display", _DisplayTable(_ROOMY_DISPLAY)):
        frame = _FrameDouble(size=size, position=position)

        fit_frame_to_screen(frame, (1100, 780))

    rect = wx.Rect(*_ROOMY_DISPLAY)
    assert frame.size.width <= size[0]
    assert frame.size.height <= size[1]
    assert rect.Contains(frame.position) is True
    assert frame.position.x + frame.size.width <= rect.x + rect.width
    assert frame.position.y + frame.size.height <= rect.y + rect.height


# --------------------------------------------- the console's own uses


def test_main_frame_geometry_given_no_saved_geometry_opens_at_min_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LoadFrame`` ignores main.xrc's <size>; the ctor applies it."""
    _stub_display(monkeypatch, _ROOMY_DISPLAY)
    frame = _FrameDouble(size=(429, 373))
    view = _bare_view(frame)

    view._apply_frame_geometry(None)

    assert (frame.size.width, frame.size.height) == main_frame.MIN_SIZE


def test_main_frame_geometry_given_a_saved_geometry_restores_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E8.1.1: a relaunch opens where the operator left the console."""
    _stub_display(monkeypatch, _ROOMY_DISPLAY)
    frame = _FrameDouble()
    view = _bare_view(frame)

    view._apply_frame_geometry((40, 60, 1280, 900))

    assert (frame.position.x, frame.position.y) == (40, 60)
    assert (frame.size.width, frame.size.height) == (1280, 900)


def test_main_frame_geometry_given_an_oversize_geometry_clamps_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persisted size from a larger display never lands off-screen."""
    _stub_display(monkeypatch, _SMALL_DISPLAY)
    frame = _FrameDouble()
    view = _bare_view(frame)

    view._apply_frame_geometry((100, 50, 4000, 3000))

    assert (frame.size.width, frame.size.height) == (1366 - 32, 768 - 32)
    assert (frame.position.x, frame.position.y) == (32, 50)


def test_main_frame_geometry_given_a_stale_negative_position_pulls_it_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A geometry saved on a since-disconnected display comes back."""
    _stub_display(monkeypatch, _ROOMY_DISPLAY)
    frame = _FrameDouble()
    view = _bare_view(frame)

    view._apply_frame_geometry((-1800, -400, 1100, 780))

    assert (frame.position.x, frame.position.y) == (0, 25)


def test_main_frame_display_change_refits_the_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A monitor change re-runs the fit on the frame's new display."""
    display = _stub_display(monkeypatch, _ROOMY_DISPLAY)
    frame = _FrameDouble(size=(1100, 780), position=(100, 100))
    view = _bare_view(frame)
    event = _RecordingEvent()

    view._on_display_changed(event)

    assert (display.requested, event.skipped) == ([0], True)


def test_main_frame_display_change_given_a_smaller_monitor_clamps_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A smaller monitor cannot leave a window larger than it."""
    _stub_display(monkeypatch, _SMALL_DISPLAY)
    frame = _FrameDouble(size=(1920, 1080), position=(100, 100))
    view = _bare_view(frame)

    view._on_display_changed(_RecordingEvent())

    assert (frame.size.width, frame.size.height) == (1366 - 32, 768 - 32)


def test_main_frame_init_binds_the_display_change_refit() -> None:
    """The constructor wires EVT_DISPLAY_CHANGED to the re-fit handler.

    A real ``wx.Frame`` needs a desktop, so the wiring is pinned on the
    constructor's own source -- the same transcription contract
    ``test_main_frame_guard.py`` holds REQUIRED_CONTROLS to, and the
    only headless way to prove the binding exists at all.

    logic-coverage-exempt: T-3 -- the binding cannot be exercised as a
    behaviour here, because ``wx.Bind`` needs a real ``wx.Frame`` and
    the unit process never builds one. The handler the binding names is
    covered behaviourally by the two ``_on_display_changed`` tests
    above; this pin covers only the wiring itself.
    """
    source = inspect.getsource(main_frame.MainFrame.__init__)

    assert "wx.EVT_DISPLAY_CHANGED" in source
    assert "self._on_display_changed" in source
