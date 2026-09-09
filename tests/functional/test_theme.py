# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for appearance modes: live SetAppearance wiring.

R-03: the Settings window's System/Light/Dark appearance radios apply
the OS appearance at runtime via ``wx.App.SetAppearance``. W13
(testing notes #14) removed the View > Theme menu trio -- the
Settings radios are the single theme surface -- so every scenario
drives ``settings_dlg``'s radios + OK (``theme.apply_mode``), never a
View-menu event. Appearance is process-global state -- the same
reasoning ``test_quit_flow_wx.py`` gives for quitting -- so every
behaviour-mutating case here runs in a fresh, spawned interpreter via
``console_subprocess_scenarios.py``, following that module's own
isolation rationale exactly (reproduced rather than shared, per that
module's own note about this task's file batch having no room for a
shared sibling helper).

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

from rivercrossing.ui import theme

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

    ux-polish: the last three rows pin the Light-detection probe
    ``apply_light_mode_panel_bg`` depends on -- ``wx.SystemSettings.
    GetAppearance()`` exists and returns a ``wx.SystemAppearance``
    that exposes ``IsDark()`` but **no** ``IsLight()``, so Light is
    measured as ``not IsDark()`` (theme.py's own helper docstring).
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
        hasattr(wx.SystemSettings, "GetAppearance"),
        hasattr(wx.SystemAppearance, "IsDark"),
        hasattr(wx.SystemAppearance, "IsLight"),
    )

    assert checks == (True, True, True, True, True, True, True, False, True, True, False)


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
def test_theme_dark_applies_at_runtime_and_records_the_mode_on_mac() -> None:
    """Apply Dark via Settings OK: live flip; a screenshot saves.

    macOS live-switch half of R-03: no restart, no capability check.
    """
    result = scenario_runner.run_scenario("theme_dark_applies_at_runtime")

    assert result["data"] == {
        "is_dark_after": True,
        "appearance_unchanged": False,
        "theme_mode_after": "dark",
        "notice_after": "",
        "screenshot_exists": True,
    }, result["context"]


@_WIN32_ONLY
def test_theme_dark_cannot_change_at_runtime_and_posts_the_next_launch_notice_on_windows() -> None:
    """Apply Dark via Settings OK: CannotChange leaves appearance alone.

    Never asserts ``is_dark_after``'s absolute value -- that Windows
    CI runner's own current OS theme is not knowable in advance, and
    the CannotChange contract (theme.py's own module docstring) only
    documents that the call has no runtime effect, not what the
    unrelated pre-existing appearance was. The controller's recorded
    mode and the notice hold on every platform.
    """
    result = scenario_runner.run_scenario("theme_dark_applies_at_runtime")

    assert result["data"]["appearance_unchanged"] is True, result["context"]
    assert result["data"]["theme_mode_after"] == "dark", result["context"]
    assert result["data"]["notice_after"] == theme._NEXT_LAUNCH_NOTICE, result["context"]


@_DARWIN_ONLY
def test_theme_light_round_trip_restores_light_appearance_on_mac() -> None:
    """Dark then Light through Settings OK: appearance and mode flip."""
    result = scenario_runner.run_scenario("theme_light_round_trip")

    assert result["data"] == {
        "is_dark_after": False,
        "theme_mode_after": "light",
        "notice_after": "",
    }, result["context"]


@_WIN32_ONLY
def test_theme_light_round_trip_cannot_change_on_windows() -> None:
    """Dark then Light: CannotChange means no runtime effect."""
    result = scenario_runner.run_scenario("theme_light_round_trip")

    assert result["data"]["theme_mode_after"] == "light", result["context"]
    assert result["data"]["notice_after"] == theme._NEXT_LAUNCH_NOTICE, result["context"]


@_DARWIN_ONLY
def test_theme_system_reapplies_on_sys_colour_changed_bounded_by_the_guard_on_mac() -> None:
    """Dark then System via Settings OK: a bounded, guarded re-apply.

    Dark first is deliberate: measured, a same-value ``SetAppearance``
    call does not re-fire ``EVT_SYS_COLOUR_CHANGED`` on this pin, so a
    run that never leaves System would exercise no reentrant path at
    all. ``apply_call_count`` == 3 is itself measured against the real
    ``ThemeController`` -- one call for Dark (mode isn't System, so
    ``on_sys_colour_changed`` takes no further action), and two for
    System (the settings OK's own apply, plus exactly one guarded
    re-apply the resulting ``EVT_SYS_COLOUR_CHANGED`` triggers -- a
    would-be third, nested call never happens on this pin, since the
    guarded re-apply's ``SetAppearance(System)`` is itself a same-value
    call once the first one already landed).
    """
    result = scenario_runner.run_scenario("theme_system_reapplies_on_sys_colour_changed")

    assert result["data"] == {"apply_call_count": 3, "theme_mode_after": "system"}, result[
        "context"
    ]


@_WIN32_ONLY
def test_theme_system_settings_ok_still_applies_and_records_the_mode_on_windows() -> None:
    """Dark then System through Settings OK still apply on Windows.

    Never asserts an exact ``apply_call_count``: whether MSW re-fires
    ``EVT_SYS_COLOUR_CHANGED`` from inside a ``CannotChange``
    ``SetAppearance`` call is not documented anywhere this design
    depends on (theme.py's own module docstring measures only the
    macOS reentrancy pinned above), so only the floor of two Settings
    OK applies -- guaranteed by construction, one ``apply_mode`` call
    each -- is asserted, regardless of any further reentrant calls
    this pin may or may not add.
    """
    result = scenario_runner.run_scenario("theme_system_reapplies_on_sys_colour_changed")

    assert result["data"]["apply_call_count"] >= 2, result["context"]
    assert result["data"]["theme_mode_after"] == "system", result["context"]


# --- ux-polish: the light-mode panel background ----------------------
#
# The same spawned-subprocess isolation as every live-appearance
# scenario above: the dialog tint is decided from
# ``wx.SystemSettings.GetAppearance()``, process-global state, so the
# probe runs in its own fresh interpreter. The scenario
# (``ride_setup_dlg_light_panel_background``) applies the Light radio
# through the Settings dialog's OK (the W13 single theme surface),
# opens the real ``mi_new_ride`` route, and records the shown dialog's
# background colour plus the live appearance.

_PANEL_BG_SCENARIO = "ride_setup_dlg_light_panel_background"
_PANEL_BG_RGBA = [*theme._LIGHT_PANEL_BG, 255]


@_DARWIN_ONLY
def test_ride_setup_dlg_carries_the_light_panel_background_in_light_mode_on_mac() -> None:
    """Ride Setup opened in a Light appearance shows the panel tone.

    macOS applies the Settings-applied Light live (W13), so forcing it
    guarantees ``IsDark()`` reads False while the dialog is shown, and
    the dialog's background must then be exactly
    ``theme._LIGHT_PANEL_BG`` (+ opaque alpha) -- the non-default
    panel tone that keeps the native white entry boxes distinct. The
    route also destroys the dialog after Cancel, so no stale window
    leaks into later assertions.
    """
    result = scenario_runner.run_scenario(_PANEL_BG_SCENARIO)

    assert result["ok"], result["context"]
    data = result["data"]
    assert data["dlg_shown"] is True, result["context"]
    assert data["is_dark_at_open"] is False, result["context"]
    assert data["panel_bg"] == _PANEL_BG_RGBA, result["context"]
    assert data["dialog_destroyed"] is True, result["context"]


@_WIN32_ONLY
def test_ride_setup_dlg_background_follows_the_unchangeable_appearance_on_windows() -> None:
    """The tint tracks the rendered appearance; a dark OS never tints.

    Never asserts ``is_dark_at_open``'s absolute value -- that Windows
    CI runner's own current OS theme is not knowable in advance, and
    the CannotChange contract means the Settings-applied Light radio
    cannot alter it. The invariant holds on both outcomes: the dialog
    carries the panel tone exactly when the live appearance reads
    Light (a Light OS tints -- its entry boxes are white; a Dark OS
    stays fully native -- dark mode is unchanged by design).
    """
    result = scenario_runner.run_scenario(_PANEL_BG_SCENARIO)

    assert result["ok"], result["context"]
    data = result["data"]
    assert data["dlg_shown"] is True, result["context"]
    assert (data["panel_bg"] == _PANEL_BG_RGBA) is (data["is_dark_at_open"] is False), result[
        "context"
    ]
    assert data["dialog_destroyed"] is True, result["context"]
