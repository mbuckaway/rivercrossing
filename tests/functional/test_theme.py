# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for View > Theme: live SetAppearance wiring (Phase 8, 8.6).

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
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import wx

from rivercrossing.ui import commands

pytestmark = pytest.mark.functional

SCENARIOS_SCRIPT = Path(__file__).resolve().parent / "console_subprocess_scenarios.py"
SCENARIO_TIMEOUT_SECONDS = 30
SCENARIO_SPAWN_ATTEMPTS = 3


def _spawn_scenario(name: str) -> subprocess.CompletedProcess[str]:
    """Spawn one fresh interpreter running scenario *name*.

    Always ``subprocess`` (spawn), never ``os.fork``: forking a
    process that may already have an initialised ``NSApplication`` is
    unsafe on macOS, and this session's own ``wx_app`` fixture usually
    already has one.
    """
    return subprocess.run(  # noqa: S603 -- sys.executable + a fixed repo-local script path
        [sys.executable, str(SCENARIOS_SCRIPT), name],
        capture_output=True,
        text=True,
        timeout=SCENARIO_TIMEOUT_SECONDS,
        check=False,
    )


def _decode_scenario_output(
    name: str, completed: subprocess.CompletedProcess[str]
) -> dict[str, Any]:
    """Decode *completed*'s stdout into the scenario's JSON envelope."""
    context = (
        f"scenario={name!r} returncode={completed.returncode}\n"
        f"--- child stdout ---\n{completed.stdout}\n"
        f"--- child stderr ---\n{completed.stderr}"
    )
    last_line = next(
        (line for line in reversed(completed.stdout.splitlines()) if line.strip()), ""
    )
    try:
        result = json.loads(last_line)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": f"no parseable JSON on stdout: {exc}",
            "data": None,
            "context": context,
        }
    result["context"] = context
    return result


def _run_scenario(name: str) -> dict[str, Any]:
    """Run scenario *name* in a fresh interpreter; decode its result.

    Retries the spawn itself: measured elsewhere in this suite (
    ``test_console_demo.py``'s own precedent), a whole process launch
    can rarely land on a memory layout where every in-process attempt
    fails, and a fresh spawn gets an independent layout.
    """
    result: dict[str, Any] = {"ok": False, "error": "no attempt ran", "data": None, "context": ""}
    for _attempt in range(SCENARIO_SPAWN_ATTEMPTS):
        try:
            completed = _spawn_scenario(name)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            result = {
                "ok": False,
                "error": f"child timed out after {SCENARIO_TIMEOUT_SECONDS}s",
                "data": None,
                "context": f"scenario={name!r}\nstdout={stdout}\nstderr={stderr}",
            }
            continue
        result = _decode_scenario_output(name, completed)
        if result["ok"]:
            return result
    return result


# --- the spelling probe (8.6.1) -------------------------------------


def test_wx_pyapp_appearance_enums_exist_at_the_pinned_wx() -> None:
    """Pins the exact spellings ``theme.py`` depends on (measured 4.3.1).

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


def test_theme_dark_applies_at_runtime_and_keeps_the_radio_checked() -> None:
    """mi_theme_dark flips SystemAppearance live; a screenshot is saved.

    macOS live-switch half of P8-D4: no restart, no capability check.
    """
    result = _run_scenario("theme_dark_applies_at_runtime")

    assert result["data"] == {
        "is_dark": True,
        "radio_checked": True,
        "screenshot_exists": True,
    }, result["context"]


def test_theme_light_round_trip_restores_light_appearance() -> None:
    """Dark then Light: SystemAppearance and the radio both flip back."""
    result = _run_scenario("theme_light_round_trip")

    assert result["data"] == {"is_dark": False, "radio_checked": True}, result["context"]


def test_theme_system_reapplies_on_sys_colour_changed_bounded_by_the_guard() -> None:
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
    result = _run_scenario("theme_system_reapplies_on_sys_colour_changed")

    assert result["data"] == {"apply_call_count": 3, "radio_checked": True}, result["context"]


def test_theme_ids_post_no_stub_notice_while_zoom_ids_still_do() -> None:
    """Theme ids stay silent (Ok, macOS); mi_zoom_110 still posts the stub."""
    result = _run_scenario("theme_ids_do_not_post_the_stub_notice_but_zoom_still_does")

    expected_zoom_notice = f"{commands.route_for_id('mi_zoom_110').label} — not yet implemented"
    assert result["data"] == {
        "theme_notice_unchanged": True,
        "zoom_stub_notice": expected_zoom_notice,
    }, result["context"]
