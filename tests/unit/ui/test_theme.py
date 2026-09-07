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

ux-polish: :func:`theme.apply_light_mode_panel_bg` follows the same
split. Its Light decision keys off ``wx.SystemSettings.
GetAppearance()``, which needs a live ``wx.App`` (measured -- calling
it headless raises ``PyNoAppError``), so the unit tests below drive
the decision through a monkeypatched ``theme.require_wx`` returning a
fake wx module and a plain-object fake dialog, exactly as
:func:`theme.apply`'s own capability-guard tests patch ``wx.PyApp``.
The real-wx proof -- ``ride_setup_dlg`` carries the panel tone in a
Light appearance -- lives in the spawned-subprocess scenarios in
``tests/functional/test_theme.py``.
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


# --- menu_item_id_for: the reverse mapping (E8.1.1) -----------------

MENU_ITEM_ID_FOR_CASES = (
    (theme.ThemeMode.SYSTEM, ids.MI_THEME_SYSTEM),
    (theme.ThemeMode.LIGHT, ids.MI_THEME_LIGHT),
    (theme.ThemeMode.DARK, ids.MI_THEME_DARK),
)


@pytest.mark.parametrize(("mode", "expected_item_id"), MENU_ITEM_ID_FOR_CASES)
def test_menu_item_id_for_given_each_mode_returns_its_radio(
    mode: theme.ThemeMode, expected_item_id: str
) -> None:
    """The reverse mapping: each mode names its own theme radio."""
    result = theme.menu_item_id_for(mode)

    assert result == expected_item_id


@pytest.mark.parametrize("item_id", theme.THEME_MENU_ITEM_IDS)
def test_menu_item_id_for_inverts_mode_for_menu_id_given_each_declared_id(
    item_id: str,
) -> None:
    """The two mappings round-trip over every declared theme radio."""
    result = theme.menu_item_id_for(theme.mode_for_menu_id(item_id))

    assert result == item_id


# --- ThemeController: the E8.1.1 persisted-mode constructor ---------


class _FakeThemeApp:
    """A wx.App double recording every ``SetAppearance`` call."""

    def __init__(self) -> None:
        """Start with an empty appearance call log."""
        self.appearances: list[object] = []

    def SetAppearance(self, appearance: object) -> object:  # noqa: N802 -- wx.App's own name, the API theme.apply calls
        """Record the requested appearance and report success."""
        self.appearances.append(appearance)
        return wx.PyApp.AppearanceResult.Ok


def test_theme_controller_constructed_with_dark_mode_applies_dark() -> None:
    """A persisted Dark mode applies at construction (E8.1.1)."""
    fake = _FakeThemeApp()

    controller = theme.ThemeController(fake, mode=theme.ThemeMode.DARK)

    assert controller.mode is theme.ThemeMode.DARK
    assert fake.appearances == [wx.PyApp.Appearance.Dark]


def test_theme_controller_constructed_with_system_mode_applies_nothing() -> None:
    """System is the OS default; constructing with it stays silent."""
    fake = _FakeThemeApp()

    controller = theme.ThemeController(fake, mode=theme.ThemeMode.SYSTEM)

    assert controller.mode is theme.ThemeMode.SYSTEM
    assert fake.appearances == []


# --- apply: the E8.1.2 capability guard -----------------------------


class _NoSetAppearanceApp:
    """A wx.App double with no ``SetAppearance`` at all (E8.1.2)."""


class _NoAppearancePyApp:
    """A wx.PyApp stand-in exposing no ``Appearance`` enum (E8.1.2)."""


def test_apply_returns_none_when_the_app_lacks_set_appearance() -> None:
    """A build regressing SetAppearance away falls back silently."""
    result = theme.apply(_NoSetAppearanceApp(), theme.ThemeMode.DARK)

    assert result is None


def test_apply_returns_none_when_wx_pyapp_appearance_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wx build exposing no wx.PyApp.Appearance also falls back."""
    monkeypatch.setattr(wx, "PyApp", _NoAppearancePyApp)

    result = theme.apply(_FakeThemeApp(), theme.ThemeMode.DARK)

    assert result is None


# --- apply_light_mode_panel_bg: the light-only panel tint (ux-polish)
#
# The Light decision is ``not wx.SystemSettings.GetAppearance().
# IsDark()``: measured at the pinned wxPython 4.3.1, GetAppearance()
# returns a wx.SystemAppearance that exposes IsDark()/IsSystemDark()/
# IsUsingDarkBackground()/GetName()/AreAppsDark but NO IsLight()
# (theme.py's own helper docstring records the measurement). The
# functional suite's scenarios already read live theme results with
# the identical probe -- ``GetAppearance().IsDark()`` -- so these
# tests pin the helper's branch decision through a fake wx module,
# never a real wx.App (module docstring).


class _FakePanelDialog:
    """A wx.Dialog double recording every SetBackgroundColour call."""

    def __init__(self) -> None:
        self.backgrounds: list[object] = []

    def SetBackgroundColour(self, colour: object) -> None:  # noqa: N802 -- wx.Window's own API name, what the helper calls
        self.backgrounds.append(colour)


class _FakeAppearance:
    """A wx.SystemAppearance double with one fixed dark/light state."""

    def __init__(self, *, dark: bool) -> None:
        self._dark = dark

    def IsDark(self) -> bool:  # noqa: N802 -- wx.SystemAppearance's own API name, what the helper calls
        return self._dark


class _FakeSystemSettings:
    """A wx.SystemSettings double returning one fixed appearance."""

    def __init__(self, *, dark: bool) -> None:
        self._appearance = _FakeAppearance(dark=dark)

    def GetAppearance(self) -> _FakeAppearance:  # noqa: N802 -- wx.SystemSettings's own API name, what the helper calls
        return self._appearance


class _FakeWx:
    """A minimal wx double: an appearance probe plus a Colour factory.

    ``Colour`` echoes its RGB arguments unchanged, so the recorded
    dialog background equals the exact RGB the helper asked for.
    """

    def __init__(self, *, dark: bool) -> None:
        self.SystemSettings = _FakeSystemSettings(dark=dark)
        self.colour_rgb: list[tuple[int, int, int]] = []

    def Colour(self, red: int, green: int, blue: int) -> tuple[int, int, int]:  # noqa: N802 -- wx.Colour's own API name, what the helper calls
        self.colour_rgb.append((red, green, blue))
        return (red, green, blue)


def test_apply_light_mode_panel_bg_given_light_appearance_sets_the_panel_tone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Light appearance (Light radio, or System on a light OS): tint."""
    dialog = _FakePanelDialog()
    fake_wx = _FakeWx(dark=False)
    monkeypatch.setattr(theme, "require_wx", lambda: fake_wx)

    theme.apply_light_mode_panel_bg(dialog)

    assert dialog.backgrounds == [tuple(theme._LIGHT_PANEL_BG)]
    assert fake_wx.colour_rgb == [tuple(theme._LIGHT_PANEL_BG)]


def test_apply_light_mode_panel_bg_given_dark_appearance_leaves_background_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dark appearance (Dark radio, or System on a dark OS): no-op."""
    dialog = _FakePanelDialog()
    fake_wx = _FakeWx(dark=True)
    monkeypatch.setattr(theme, "require_wx", lambda: fake_wx)

    theme.apply_light_mode_panel_bg(dialog)

    assert dialog.backgrounds == []
    assert fake_wx.colour_rgb == []


def test_light_panel_bg_constant_is_the_neutral_light_grey_tone() -> None:
    """The panel tone is a single neutral light grey, not white."""
    assert theme._LIGHT_PANEL_BG == (230, 230, 230)
