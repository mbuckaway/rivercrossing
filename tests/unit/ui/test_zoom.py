# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the zoom ladder mapping and scaling math (E8.1.4).

``ui.zoom`` mirrors ``ui.theme``'s split (its own module docstring):
``percent_for_menu_id``/``menu_item_id_for``/``scaled_point_size`` are
fully wx-free, so the seven-way mapping, its negative path, and the
point-size rounding are exactly the logic R-71's >=90% branch-coverage
gate is meant to cover. Everything past that point -- the per-window
base-font capture and the real ``wx.Font`` construction -- is proven
only by the spawned-subprocess scenarios in
``tests/functional/test_settings.py`` instead (the same split
``test_theme.py`` draws).
"""

import re
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui import ids, zoom

# --- percent_for_menu_id: all seven zoom radios (T-3/T-13) ----------

PERCENT_FOR_MENU_ID_CASES = (
    (ids.MI_ZOOM_90, 90),
    (ids.MI_ZOOM_100, 100),
    (ids.MI_ZOOM_110, 110),
    (ids.MI_ZOOM_120, 120),
    (ids.MI_ZOOM_130, 130),
    (ids.MI_ZOOM_140, 140),
    (ids.MI_ZOOM_150, 150),
)


@pytest.mark.parametrize(("item_id", "expected_percent"), PERCENT_FOR_MENU_ID_CASES)
def test_percent_for_menu_id_given_each_zoom_radio_returns_its_percent(
    item_id: str, expected_percent: int
) -> None:
    """Each of the seven zoom radios maps to its own ladder percent."""
    result = zoom.percent_for_menu_id(item_id)

    assert result == expected_percent


def test_percent_for_menu_id_given_an_unknown_id_raises_naming_it() -> None:
    """T-5: the negative path for an id outside the zoom ladder."""
    fake_id = "mi_totally_fake_zoom_id_not_a_zoom_radio"

    with pytest.raises(zoom.UnknownZoomMenuItemError, match=re.escape(fake_id)):
        zoom.percent_for_menu_id(fake_id)


def test_zoom_menu_item_ids_declare_exactly_the_seven_zoom_radios() -> None:
    """The public id tuple is the ladder, in ladder order."""
    result = zoom.ZOOM_MENU_ITEM_IDS

    assert result == (
        ids.MI_ZOOM_90,
        ids.MI_ZOOM_100,
        ids.MI_ZOOM_110,
        ids.MI_ZOOM_120,
        ids.MI_ZOOM_130,
        ids.MI_ZOOM_140,
        ids.MI_ZOOM_150,
    )


@given(st.sampled_from(zoom.ZOOM_MENU_ITEM_IDS))
def test_percent_for_menu_id_given_any_declared_id_round_trips_to_that_id(
    item_id: str,
) -> None:
    """T-7: percent lookup inverted by menu_item_id_for is identity."""
    assert zoom.menu_item_id_for(zoom.percent_for_menu_id(item_id)) == item_id


# --- menu_item_id_for: the reverse mapping --------------------------

MENU_ITEM_ID_FOR_CASES = (
    (90, ids.MI_ZOOM_90),
    (100, ids.MI_ZOOM_100),
    (110, ids.MI_ZOOM_110),
    (120, ids.MI_ZOOM_120),
    (130, ids.MI_ZOOM_130),
    (140, ids.MI_ZOOM_140),
    (150, ids.MI_ZOOM_150),
)


@pytest.mark.parametrize(("percent", "expected_item_id"), MENU_ITEM_ID_FOR_CASES)
def test_menu_item_id_for_given_each_percent_returns_its_radio(
    percent: int, expected_item_id: str
) -> None:
    """The reverse mapping: each percent names its own zoom radio."""
    result = zoom.menu_item_id_for(percent)

    assert result == expected_item_id


@pytest.mark.parametrize("item_id", zoom.ZOOM_MENU_ITEM_IDS)
def test_menu_item_id_for_inverts_percent_for_menu_id_given_each_declared_id(
    item_id: str,
) -> None:
    """The two mappings round-trip over every declared zoom radio."""
    result = zoom.menu_item_id_for(zoom.percent_for_menu_id(item_id))

    assert result == item_id


# --- scaled_point_size: round(base * percent / 100), floored at 1 ---

SCALED_POINT_SIZE_CASES = (
    (13, 100, 13),
    (13, 120, 16),  # round(15.6)
    (13, 90, 12),  # round(11.7)
    (13, 150, 20),  # round(19.5) -- banker's rounding to the even
    (20, 120, 24),
    (20, 90, 18),
    (0, 100, 1),  # floored at 1: a degenerate 0% still yields a font
)


@pytest.mark.parametrize(("base", "percent", "expected"), SCALED_POINT_SIZE_CASES)
def test_scaled_point_size_given_each_base_and_percent_rounds_the_product(
    base: int, percent: int, expected: int
) -> None:
    """The font point size is round(base * percent / 100), min 1."""
    result = zoom.scaled_point_size(base, percent)

    assert result == expected


# --- ZoomController: the percent state (apply touches wx) -----------


def test_zoom_controller_starts_at_the_given_percent() -> None:
    """The controller's percent state is set by the constructor."""
    controller = zoom.ZoomController(percent=120)

    assert controller.percent == 120


def test_zoom_controller_defaults_to_one_hundred_percent() -> None:
    """100% is the checked menu default (E8.1.1's _check_default)."""
    controller = zoom.ZoomController()

    assert controller.percent == 100


# --- the font-scaling half: zoom.require_wx stays a fake seam -------
# test_theme.py's own split draws the line this follows: the wx-free
# mapping math is proven headless, and the real ``wx.Font`` build is
# driven through a monkeypatched ``zoom.require_wx`` returning a fake
# wx module -- never a live wx.App in the unit process. The spawned-
# subprocess scenarios in tests/functional/test_settings.py are where
# the real fonts render.


class _FakeBaseFont:
    """A wx.Font double answering the metric accessors zoom reads."""

    def __init__(self, point_size: int) -> None:
        """Fix every metric; point size is what scaling varies."""
        self._point_size = point_size
        self.family = "FAMILY"
        self.style = "STYLE"
        self.weight = "WEIGHT"
        self.underlined = False
        self.face_name = "FACENAME"

    def GetPointSize(self) -> int:  # noqa: N802 -- wx.Font's own API name
        """Return the base point size."""
        return self._point_size

    def GetFamily(self) -> str:  # noqa: N802 -- wx.Font's own API name
        """Return the fixed family marker."""
        return self.family

    def GetStyle(self) -> str:  # noqa: N802 -- wx.Font's own API name
        """Return the fixed style marker."""
        return self.style

    def GetWeight(self) -> str:  # noqa: N802 -- wx.Font's own API name
        """Return the fixed weight marker."""
        return self.weight

    def GetUnderlined(self) -> bool:  # noqa: N802 -- wx.Font's own API name
        """Return the fixed underline flag."""
        return self.underlined

    def GetFaceName(self) -> str:  # noqa: N802 -- wx.Font's own API name
        """Return the fixed face-name marker."""
        return self.face_name


class _FakeScaledFont:
    """A wx.Font construction record: the six positional arguments."""

    def __init__(self, args: tuple[object, ...]) -> None:
        """Store the six wx.Font arguments exactly as given."""
        self.args = args


class _FakeWx:
    """A minimal wx double: top-level windows plus a Font factory."""

    def __init__(self, top_level_windows: tuple[Any, ...]) -> None:
        """Fix the window list and start an empty font log."""
        self._top_level_windows = top_level_windows
        self.font_calls: list[_FakeScaledFont] = []

    def GetTopLevelWindows(self) -> tuple[Any, ...]:  # noqa: N802 -- wx's own API name, what ZoomController.apply calls
        """Return the fixed open-window list."""
        return self._top_level_windows

    def Font(self, *args: object) -> _FakeScaledFont:  # noqa: N802 -- wx's own factory name, what _scaled_font calls
        """Record one font build and return its record."""
        font = _FakeScaledFont(args)
        self.font_calls.append(font)
        return font


class _FakeWindow:
    """A wx.Window double: fixed base font, children, SetFont log."""

    def __init__(self, base_point_size: int, *, children: tuple[Any, ...] = ()) -> None:
        """Fix the base font and child list; start an empty font log."""
        self._base_font = _FakeBaseFont(base_point_size)
        self._children = list(children)
        self.set_fonts: list[_FakeScaledFont] = []

    def GetFont(self) -> _FakeBaseFont:  # noqa: N802 -- wx.Window's own API name, what _apply_fonts reads
        """Return the fixed base font."""
        return self._base_font

    def GetChildren(self) -> list[Any]:  # noqa: N802 -- wx.Window's own API name
        """Return the fixed child list."""
        return self._children

    def SetFont(self, font: _FakeScaledFont) -> None:  # noqa: N802 -- wx.Window's own API name
        """Record the scaled font this window was given."""
        self.set_fonts.append(font)


def test_zoom_controller_apply_scales_every_open_window_and_its_descendants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """apply() walks top-level windows and recurses into children."""
    child = _FakeWindow(11)
    window = _FakeWindow(13, children=(child,))
    other = _FakeWindow(9)  # a second top-level window, no children
    fake_wx = _FakeWx((window, other))
    monkeypatch.setattr(zoom, "require_wx", lambda: fake_wx)
    controller = zoom.ZoomController(percent=100)

    controller.apply(120)

    assert controller.percent == 120
    assert len(window.set_fonts) == 1
    scaled = window.set_fonts[0]
    assert scaled.args == (
        round(13 * 120 / 100),
        "FAMILY",
        "STYLE",
        "WEIGHT",
        False,
        "FACENAME",
    )
    assert window._zoom_base_font is window._base_font
    assert len(child.set_fonts) == 1
    assert child.set_fonts[0].args == (
        round(11 * 120 / 100),
        "FAMILY",
        "STYLE",
        "WEIGHT",
        False,
        "FACENAME",
    )
    assert child._zoom_base_font is child._base_font
    assert [font.args[0] for font in other.set_fonts] == [round(9 * 120 / 100)]
    assert other._zoom_base_font is other._base_font


def test_zoom_controller_apply_to_scales_from_the_captured_base_font(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A re-apply scales the stored base, never the live font again."""
    window = _FakeWindow(13)
    window._zoom_base_font = _FakeBaseFont(10)  # captured on an earlier apply
    monkeypatch.setattr(zoom, "require_wx", lambda: _FakeWx(()))
    controller = zoom.ZoomController(percent=120)

    controller.apply_to(window)

    assert [font.args[0] for font in window.set_fonts] == [round(10 * 120 / 100)]
    assert window._zoom_base_font.GetPointSize() == 10


def test_zoom_controller_apply_with_no_open_windows_is_a_silent_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With zero open windows apply only records the new percent."""
    fake_wx = _FakeWx(())
    monkeypatch.setattr(zoom, "require_wx", lambda: fake_wx)
    controller = zoom.ZoomController(percent=100)

    controller.apply(130)

    assert controller.percent == 130
    assert fake_wx.font_calls == []


def test_zoom_set_percent_scales_open_windows_via_the_default_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``zoom.set_percent`` drives the one default controller."""
    window = _FakeWindow(13)
    fake_wx = _FakeWx((window,))
    monkeypatch.setattr(zoom, "require_wx", lambda: fake_wx)
    monkeypatch.setattr(zoom, "_default_controller", zoom.ZoomController(percent=100))

    zoom.set_percent(150)

    assert zoom._default_controller.percent == 150
    assert [font.args[0] for font in window.set_fonts] == [round(13 * 150 / 100)]


def test_zoom_apply_to_scales_one_window_via_the_default_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``zoom.apply_to`` scales one window at the current percent."""
    window = _FakeWindow(13)
    monkeypatch.setattr(zoom, "require_wx", lambda: _FakeWx(()))
    monkeypatch.setattr(zoom, "_default_controller", zoom.ZoomController(percent=90))

    zoom.apply_to(window)

    assert [font.args[0] for font in window.set_fonts] == [round(13 * 90 / 100)]
    assert window._zoom_base_font is window._base_font
