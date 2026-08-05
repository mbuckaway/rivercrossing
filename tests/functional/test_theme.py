# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for View > Theme: live SetAppearance wiring (Phase 8).

R-03/P8-D4: the three theme radios apply the OS appearance at runtime
via ``wx.App.SetAppearance``. Appearance is process-global state --
the same reasoning ``test_quit_flow_wx.py`` gives for quitting -- so
every behaviour-mutating case here runs in a fresh, spawned
interpreter via ``console_subprocess_scenarios.py``, following that
module's own isolation rationale exactly (reproduced rather than
shared, per that module's own note about this task's file batch
having no room for a shared sibling helper).

The one test that is *not* a subprocess scenario is the spelling
probe below: it pins the exact ``wx.PyApp.Appearance`` /
``wx.PyApp.AppearanceResult`` spellings ``theme.py`` depends on, and
is allowed to be green in this module's own red commit -- it pins an
API this design depends on, not a behaviour under test.

Phase 10 splits the four live-switch scenarios' own tests by
platform: macOS applies every mode at runtime (live, no restart, no
capability check -- theme.py's own module docstring); MSW returns
``AppearanceResult.CannotChange`` once ``main_frame`` already exists,
so a Windows run never actually changes ``SystemSettings.
GetAppearance()`` and instead posts ``theme._NEXT_LAUNCH_NOTICE`` on
the status bar. Each scenario now returns enough raw facts for both
contracts; the darwin-only test below asserts the same absolute
values it always has, and the win32-only test asserts only what
theme.py's own docstring documents for MSW -- never a live Windows
runtime value (e.g. that machine's own current OS theme) this design
has no way to know in advance.
"""

import sys

import pytest
import scenario_runner
import wx

from rivercrossing.ui import commands, theme

pytestmark = pytest.mark.functional


# --- the spelling probe (8.6.1) -------------------------------------


def test_wx_pyapp_appearance_enums_exist_at_the_pinned_wx() -> None:
    """Pins the exact spellings ``theme.py`` depends on (measured).

    ``wx.Appearance`` does **not** exist at this pin; ``wx.PyApp.
    Appearance`` and ``wx.PyApp.AppearanceResult`` do, and ``wx.App``
    inherits both (``wx.App`` derives from ``wx.PyApp``). Allowed to
    be green in this module's own red commit -- an API pin, not a
    behaviour test; if any of these read differently on a future
    wxWidgets pin, ``theme.py``'s design adjusts before anything else
    depends on it.
    """
    checks = (
        hasattr(wx.PyApp.Appearance, "System"),
        hasattr(wx.PyApp.Appearance, "Light"),
        hasattr(wx.PyApp.Appearance, "Dark"),
        hasattr(wx.PyApp.AppearanceResult, "Ok"),
        hasattr(wx.PyApp.AppearanceResult, "CannotChange"),
        hasattr(wx.PyApp.AppearanceResult, "Failure"),
        hasattr(wx.App, "Appearance"),
        hasattr(wx, "Appearance"),
    )

    assert checks == (True, True, True, True, True, True, True, False)


# --- live runtime switching (subprocess: appearance is global) -----

_DARWIN_ONLY = pytest.mark.skipif(
    sys.platform != "darwin",
    reason=(
        "macOS applies SetAppearance live at runtime (theme.py's own "
        "module docstring, P8-D4); MSW's CannotChange contract "
        "(pinned by the win32-only sibling test) never lets this "
        "scenario's absolute-value assertions hold on Windows."
    ),
)
_WIN32_ONLY = pytest.mark.skipif(
    sys.platform != "win32",
    reason=(
        "Documented Windows contract: MSW returns AppearanceResult."
        "CannotChange once main_frame already exists, so the theme "
        "never actually changes at runtime (theme.py's own module "
        "docstring). RED-first: exercised on windows-latest CI, not "
        "this Mac."
    ),
)


@_DARWIN_ONLY
def test_theme_dark_applies_at_runtime_and_keeps_the_radio_checked_on_mac() -> None:
    """mi_theme_dark flips SystemAppearance live; a screenshot is saved.

    macOS live-switch half of P8-D4: no restart, no capability check.
    """
    result = scenario_runner.run_scenario("theme_dark_applies_at_runtime")

    assert result["data"] == {
        "is_dark_after": True,
        "appearance_unchanged": False,
        "radio_checked": True,
        "notice_after": "",
        "screenshot_exists": True,
    }, result["context"]


@_WIN32_ONLY
def test_theme_dark_cannot_change_at_runtime_and_posts_the_next_launch_notice_on_windows() -> None:
    """mi_theme_dark: CannotChange leaves the appearance untouched.

    Never asserts ``is_dark_after``'s absolute value -- that Windows
    CI runner's own current OS theme is not knowable in advance, and
    the CannotChange contract (theme.py's own module docstring) only
    documents that the call has no runtime effect, not what the
    unrelated pre-existing appearance was.
    """
    result = scenario_runner.run_scenario("theme_dark_applies_at_runtime")

    assert result["data"]["appearance_unchanged"] is True, result["context"]
    assert result["data"]["radio_checked"] is True, result["context"]
    assert result["data"]["notice_after"] == theme._NEXT_LAUNCH_NOTICE, result["context"]


@_DARWIN_ONLY
def test_theme_light_round_trip_restores_light_appearance_on_mac() -> None:
    """Dark then Light: SystemAppearance and the radio flip back."""
    result = scenario_runner.run_scenario("theme_light_round_trip")

    assert result["data"] == {
        "is_dark_after": False,
        "radio_checked": True,
        "notice_after": "",
    }, result["context"]


@_WIN32_ONLY
def test_theme_light_round_trip_cannot_change_on_windows() -> None:
    """Dark then Light: CannotChange means no runtime effect."""
    result = scenario_runner.run_scenario("theme_light_round_trip")

    assert result["data"]["radio_checked"] is True, result["context"]
    assert result["data"]["notice_after"] == theme._NEXT_LAUNCH_NOTICE, result["context"]


@_DARWIN_ONLY
def test_theme_system_reapplies_on_sys_colour_changed_bounded_by_the_guard_on_mac() -> None:
    """Dark then System: a bounded, guarded re-apply; mode stays System.

    Dark first is deliberate: measured, a same-value ``SetAppearance``
    call does not re-fire ``EVT_SYS_COLOUR_CHANGED`` on this pin, so a
    scenario that never leaves System would exercise no reentrant
    path at all. ``apply_call_count`` == 3 is itself measured against
    the real ``ThemeController`` -- one call for Dark (mode isn't
    System, so ``on_sys_colour_changed`` takes no further action), and
    two for System (the menu handler's own call, plus exactly one
    guarded re-apply the resulting ``EVT_SYS_COLOUR_CHANGED`` triggers
    -- a would-be third, nested call never happens on this pin, since
    the guarded re-apply's ``SetAppearance(System)`` is itself a
    same-value call once the first one already landed).
    """
    result = scenario_runner.run_scenario("theme_system_reapplies_on_sys_colour_changed")

    assert result["data"] == {"apply_call_count": 3, "radio_checked": True}, result["context"]


@_WIN32_ONLY
def test_theme_system_menu_clicks_still_apply_and_check_the_radio_on_windows() -> None:
    """Dark then System still call apply and check the radio on Windows.

    Never asserts an exact ``apply_call_count``: whether MSW re-fires
    ``EVT_SYS_COLOUR_CHANGED`` from inside a ``CannotChange``
    ``SetAppearance`` call is not documented anywhere this design
    depends on (theme.py's own module docstring measures only the
    macOS reentrancy pinned above), so only the floor two menu clicks
    guarantee by construction -- one ``on_menu`` call each -- is
    asserted, regardless of any further reentrant calls this pin may
    or may not add.
    """
    result = scenario_runner.run_scenario("theme_system_reapplies_on_sys_colour_changed")

    assert result["data"]["apply_call_count"] >= 2, result["context"]
    assert result["data"]["radio_checked"] is True, result["context"]


_THEME_VS_ZOOM_SCENARIO = "theme_ids_do_not_post_the_stub_notice_but_zoom_still_does"


@_DARWIN_ONLY
def test_theme_ids_post_no_stub_notice_while_zoom_ids_still_do_on_mac() -> None:
    """Theme ids post no notice (Ok); mi_zoom_110 still posts one."""
    result = scenario_runner.run_scenario(_THEME_VS_ZOOM_SCENARIO)

    expected_zoom_notice = f"{commands.route_for_id('mi_zoom_110').label} — not yet implemented"
    assert result["data"] == {
        "theme_notice_unchanged": True,
        "theme_notice_after": "",
        "zoom_stub_notice": expected_zoom_notice,
    }, result["context"]


@_WIN32_ONLY
def test_theme_ids_post_the_next_launch_notice_while_zoom_still_posts_stub_on_windows() -> None:
    """Theme ids post the CannotChange notice, not the generic stub."""
    result = scenario_runner.run_scenario(_THEME_VS_ZOOM_SCENARIO)

    expected_zoom_notice = f"{commands.route_for_id('mi_zoom_110').label} — not yet implemented"
    assert result["data"]["theme_notice_after"] == theme._NEXT_LAUNCH_NOTICE, result["context"]
    assert result["data"]["zoom_stub_notice"] == expected_zoom_notice, result["context"]
