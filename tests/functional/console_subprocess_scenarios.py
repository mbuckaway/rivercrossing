# SPDX-License-Identifier: GPL-3.0-only
"""Child-process program for the three subprocess-isolated tests.

Constructing many ``MainFrame``s across one long pytest session
measurably raises a wxPython 4.3.1/wxWidgets 3.3.3 address-reuse
hazard (``views/main_frame.py``'s own ``_find`` docstring): a
freshly-allocated control can transiently answer ``FindWindowByName``
with a *different*, already-destroyed control's Python type. Running
the three riskiest scenarios in a brand-new, **spawned** interpreter
each -- never forked, since forking a process that may already have
an initialised ``NSApplication`` is unsafe on macOS, and this
session's own ``wx_app`` fixture usually already has one -- removes
the *accumulated* churn a long, shared session builds up.

That alone is not the whole story, measured with throwaway sampling
scripts per this repo's convention: even fully isolated, one attempt
at the sash-round-trip sequence specifically (build, SetSashPosition,
persist, destroy, rebuild, GetSashPosition -- the one scenario of the
three that touches ``main_splitter`` twice) still hits the hazard at
a real per-attempt rate (roughly one attempt in six, sampled). The
other two measured clean at 20/20 single-attempt runs and stay as one
attempt each. :func:`_sash_round_trip` retries the *whole sequence*
-- not a single lookup, which ``main_frame.py``'s own ``_find``
already does without it helping once one lookup inside a
construction has gone wrong -- which cuts the residual rate sharply
but, sampled, does not always reach zero within one process: a rare
process launch can land on a layout where every in-process attempt
fails alike. ``test_console_demo.py``'s own ``_run_scenario`` adds a
second layer on top of this module, retrying the *spawn* itself
(a fresh process gets an independent layout) -- the two together are
what the full-suite measurement in this task's report is of.

This module *is* the child process's entire program. Run as::

    python console_subprocess_scenarios.py <scenario>

it builds its own ``wx.App`` and its own XRC resource (nothing from
the parent session's fixtures crosses a process boundary), runs one
scenario, and prints exactly one JSON line to stdout::

    {"ok": bool, "error": str | None, "data": {...} | None}

It never asserts anything itself -- ``test_console_demo.py`` decodes
this line and performs the actual comparisons, so a wrong measured
value still surfaces as a normal pytest assertion diff, not a bare
non-zero exit code.
"""

import json
import sys
from typing import TYPE_CHECKING, Any

import harness
import wx

from rivercrossing.ui import ids
from rivercrossing.ui.views import MainFrame

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["main"]


def _visible_column_titles(crossings_list: Any) -> list[str]:  # noqa: ANN401
    """Return the titles of every non-hidden column, in column order."""
    return [
        crossings_list.GetColumn(index).GetTitle()
        for index in range(crossings_list.GetColumnCount())
        if not crossings_list.GetColumn(index).IsHidden()
    ]


_SASH_ROUND_TRIP_ATTEMPTS = 5


def _sash_round_trip_once(resource: Any) -> dict[str, Any]:  # noqa: ANN401
    """One attempt at the sash round-trip; may raise ``LookupError``."""
    first_window = harness.load_window(resource, ids.MAIN_FRAME, frame=True)
    first_window.Show()
    first_window.Layout()
    harness.pump()
    first_console = MainFrame(first_window)
    first_console.main_splitter.SetSashPosition(300)
    first_console.persist_layout()
    harness.close_window(first_window)

    second_window = harness.load_window(resource, ids.MAIN_FRAME, frame=True)
    second_window.Show()
    second_window.Layout()
    harness.pump()
    try:
        second_console = MainFrame(second_window)
        restored = second_console.main_splitter.GetSashPosition()
    finally:
        harness.close_window(second_window)

    return {"restored_sash": restored}


def _sash_round_trip() -> dict[str, Any]:
    """Persist a sash position, rebuild fresh, read it back (see above).

    Retries the whole sequence: the first attempt that raises no
    ``LookupError`` wins.
    """
    resource = harness.load_xrc_resources()
    last_error: LookupError | None = None
    for _attempt in range(_SASH_ROUND_TRIP_ATTEMPTS):
        try:
            return _sash_round_trip_once(resource)
        except LookupError as exc:
            last_error = exc
    assert last_error is not None  # every iteration above sets it
    raise last_error


def _hide_times_columns_round_trip() -> dict[str, Any]:
    """Hide, then restore, the Lap time/Total columns both ways."""
    resource = harness.load_xrc_resources()
    window = harness.load_window(resource, ids.MAIN_FRAME, frame=True)
    window.Show()
    window.Layout()
    harness.pump()
    try:
        console = MainFrame(window)
        crossings_list = harness.find_control(window, ids.CROSSINGS_LIST)
        before = _visible_column_titles(crossings_list)
        console.set_hide_times(hide=True)
        during = _visible_column_titles(crossings_list)
        console.set_hide_times(hide=False)
        after = _visible_column_titles(crossings_list)
    finally:
        harness.close_window(window)
    return {"before": before, "during": during, "after": after}


def _hide_times_leaves_clock_shown() -> dict[str, Any]:
    """Toggle hide-times on; read whether the clock labels stay up."""
    resource = harness.load_xrc_resources()
    window = harness.load_window(resource, ids.MAIN_FRAME, frame=True)
    window.Show()
    window.Layout()
    harness.pump()
    try:
        console = MainFrame(window)
        console.set_hide_times(hide=True)
        elapsed_shown = harness.find_control(window, ids.CLOCK_ELAPSED_LBL).IsShown()
        remaining_shown = harness.find_control(window, ids.CLOCK_REMAINING_LBL).IsShown()
    finally:
        harness.close_window(window)
    return {"clock_elapsed_shown": elapsed_shown, "clock_remaining_shown": remaining_shown}


_SCENARIOS: dict[str, Callable[[], dict[str, Any]]] = {
    "sash_round_trip": _sash_round_trip,
    "hide_times_columns_round_trip": _hide_times_columns_round_trip,
    "hide_times_leaves_clock_shown": _hide_times_leaves_clock_shown,
}


def _run(scenario_name: str) -> dict[str, Any]:
    """Run *scenario_name*; turn any exception into an error result."""
    scenario = _SCENARIOS.get(scenario_name)
    if scenario is None:
        return {"ok": False, "error": f"unknown scenario {scenario_name!r}", "data": None}
    try:
        data = scenario()
    except Exception as exc:  # noqa: BLE001 -- reported in the envelope, not swallowed
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "data": None}
    return {"ok": True, "error": None, "data": data}


_EXPECTED_ARGC = 2  # argv[0] is the script path, argv[1] the scenario name


def main(argv: list[str]) -> int:
    """Run the scenario named in *argv*; print one JSON line to stdout.

    The JSON line on stdout *is* this program's whole contract with
    its parent (module docstring) -- printing it is not debug output
    left behind, it is the point.
    """
    if len(argv) != _EXPECTED_ARGC:
        print(  # noqa: T201 -- the child's entire contract with its parent
            json.dumps({"ok": False, "error": "usage: <script> <scenario>", "data": None})
        )
        return 2
    # Bound to a name for the rest of main(): an unbound wx.App() is
    # collected immediately (measured), and wx.GetApp() then reports
    # PyNoAppError for everything the scenario tries after it.
    app = wx.App()  # noqa: F841 -- kept alive by this binding, never read
    wx.Log.SetActiveTarget(wx.LogStderr())
    result = _run(argv[1])
    print(json.dumps(result))  # noqa: T201 -- the child's entire contract with its parent
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
