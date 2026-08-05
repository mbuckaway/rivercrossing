# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the theme mode mapping and notice text (Phase 8).

``theme.mode_for_menu_id`` is fully wx-free (module docstring), so its
three-way mapping and its negative path are exactly the kind of logic
R-71's >=90% branch-coverage gate is meant to cover -- mirrors
``test_quit_flow.py``'s own split for ``dialog_for_status``.

``theme.notice_for_result`` reasons about a real
``wx.PyApp.AppearanceResult`` enum member, so this module imports
``wx`` directly to obtain one -- the same narrow, import-only-for-a-
constant use ``test_app_wiring.py`` already established -- but never
constructs a ``wx.App``, a ``wx.Frame``, or any other live wx object:
that boundary (and everything ``theme.apply``/``ThemeController``
touch beyond it) is proven only by the real, spawned-subprocess
scenarios in ``tests/functional/test_theme.py`` instead (mirrors
``test_cards_imagelist.py``'s own split for ``CardImageList``).
"""

import re

import pytest
import wx
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui import ids, theme

# --- mode_for_menu_id: all three theme ids (T-3/T-13) ---------------

MODE_FOR_MENU_ID_CASES = (
    (ids.MI_THEME_SYSTEM, theme.ThemeMode.SYSTEM),
    (ids.MI_THEME_LIGHT, theme.ThemeMode.LIGHT),
    (ids.MI_THEME_DARK, theme.ThemeMode.DARK),
)


@pytest.mark.parametrize(("item_id", "expected_mode"), MODE_FOR_MENU_ID_CASES)
def test_mode_for_menu_id_given_each_theme_radio_returns_its_mode(
    item_id: str, expected_mode: theme.ThemeMode
) -> None:
    """Each of the three theme radios maps to its own distinct mode."""
    result = theme.mode_for_menu_id(item_id)

    assert result is expected_mode


def test_mode_for_menu_id_given_an_unknown_id_raises_naming_it() -> None:
    """T-5: the negative path for an id outside the theme trio."""
    fake_id = "mi_totally_fake_probe_id_not_a_theme_radio"

    with pytest.raises(theme.UnknownThemeMenuItemError, match=re.escape(fake_id)):
        theme.mode_for_menu_id(fake_id)


def test_theme_menu_item_ids_declares_exactly_the_three_theme_radios() -> None:
    """The public id tuple is the trio, nothing more, nothing fewer."""
    result = theme.THEME_MENU_ITEM_IDS

    assert set(result) == {ids.MI_THEME_SYSTEM, ids.MI_THEME_LIGHT, ids.MI_THEME_DARK}


@given(st.sampled_from(theme.THEME_MENU_ITEM_IDS))
def test_mode_for_menu_id_given_any_declared_id_never_raises(item_id: str) -> None:
    """Property: every id this module itself declares round-trips."""
    result = theme.mode_for_menu_id(item_id)

    assert isinstance(result, theme.ThemeMode)


# --- notice_for_result: the AppearanceResult matrix (T-3/T-13) ------

NOTICE_FOR_RESULT_CASES = (
    (wx.PyApp.AppearanceResult.CannotChange, "Theme change takes effect at next launch"),
    (wx.PyApp.AppearanceResult.Ok, None),
    (wx.PyApp.AppearanceResult.Failure, None),
)


@pytest.mark.parametrize(("result", "expected_notice"), NOTICE_FOR_RESULT_CASES)
def test_notice_for_result_given_each_appearance_result_matches_expected_notice(
    result: object, expected_notice: str | None
) -> None:
    """CannotChange (MSW, a window already open) alone gets a notice."""
    notice = theme.notice_for_result(result)

    assert notice == expected_notice
