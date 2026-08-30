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

import pytest

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
