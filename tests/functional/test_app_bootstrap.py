# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for the app bootstrap (E1.6.x): D1's runnable shell.

``build_main_window`` is the construction half of ``app.main`` --
everything ``main()`` does short of ``Show()`` and the blocking
``MainLoop`` call, split out precisely so this module can drive it
directly (``ui/app.py``'s own module docstring). Nothing here ever
calls ``MainLoop()``: the one test that must prove the real event
loop runs does so in a fresh, spawned interpreter instead, following
``console_subprocess_scenarios.py``'s own reasoning for why (this
session's ``wx_app`` fixture already has a live ``wx.App``, and a
second, unbound one is exactly the Phase-1 bug this task fixes).

Two module-scoped fixtures, mirroring ``test_console_demo.py``'s own
``shared_console`` precedent for why sharing matters here: building
``main_frame`` decodes the 53-card imagelist and constructs every
console control, and this wx build's own measured address-reuse
hazard grows with how many windows one session builds and tears down
(``MainFrame._find``'s docstring). ``bound_frame`` serves every
read-only assertion below -- including the Unbind-based route-binding
proof, which removes bindings but touches nothing else that the other
read-only assertions care about. ``firing_frame`` is a second,
independent instance for the two tests that post a real ``EVT_MENU``
event, kept separate so firing an event there can never race the
binding-removal proof over which bindings are still present.
"""

import subprocess
import sys
from typing import Any

import harness
import pytest
import wx
import wx.xrc

from rivercrossing.ui import accelerators, commands, ids
from rivercrossing.ui import app as app_module

pytestmark = pytest.mark.functional

ALL_ROUTE_ITEM_IDS = tuple(item_id for route in commands.ROUTE_TABLE for item_id in route.ids)
MENU_BOUND_ACCELERATORS = tuple(
    accelerator
    for accelerator in accelerators.ACCELERATOR_TABLE
    if accelerator.menu_item_id is not None
)

_PROBE_TIMEOUT_SECONDS = 20

# Runs main() in a fresh interpreter with wx.App.MainLoop patched to
# record whether main_frame was already shown before it can possibly
# block, then schedule a real close so the genuine MainLoop call it
# still makes actually returns -- console_subprocess_scenarios.py's
# own technique, inlined here since this task's file batch has no
# room for a shared sibling script.
_MAINLOOP_PROBE_SCRIPT = """
import json
import wx

from rivercrossing.ui import app as app_module
from rivercrossing.ui import ids

_captured = {}
_original_mainloop = wx.App.MainLoop


def _mainloop_then_close(self):
    frame = wx.Window.FindWindowByName(ids.MAIN_FRAME)
    _captured["frame_shown_before_loop"] = bool(frame is not None and frame.IsShown())
    wx.CallLater(200, frame.Close)
    return _original_mainloop(self)


wx.App.MainLoop = _mainloop_then_close
_captured["exit_code"] = app_module.main()
print(json.dumps(_captured))
"""


@pytest.fixture(scope="module")
def bound_frame(wx_app: object) -> Any:  # noqa: ANN401 -- ordering only, see docstring
    """Build the one ``main_frame`` every read-only assertion shares.

    ``wx_app`` is taken for ordering only (conftest.xrc_resource's own
    pattern): an App must exist before ``build_main_window`` runs.
    """
    frame = app_module.build_main_window(wx_app)
    try:
        yield frame
    finally:
        harness.close_window(frame)


@pytest.fixture(scope="module")
def firing_frame(wx_app: object) -> Any:  # noqa: ANN401 -- ordering only, see docstring
    """Build a second, independent ``main_frame`` for event-firing.

    Kept separate from :func:`bound_frame` so posting a real
    ``EVT_MENU`` event here can never race the Unbind-based
    route-binding proof, which removes bindings from its own frame.
    """
    frame = app_module.build_main_window(wx_app)
    try:
        yield frame
    finally:
        harness.close_window(frame)


def _fire_menu_event(frame: Any, item_id: str) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Post a real ``EVT_MENU`` for *item_id* at *frame* and pump it."""
    real_id = wx.xrc.XRCID(item_id)
    event = wx.CommandEvent(wx.EVT_MENU.typeId, real_id)
    event.SetEventObject(frame)
    frame.GetEventHandler().ProcessEvent(event)
    harness.pump()


# --- the menubar is attached (T-9) -------------------------------


def test_build_main_window_attaches_a_menubar_with_seven_menus(
    bound_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """R-73/spec §15: File, Ride, Riders, Cards, Results, View, Help."""
    menu_count = bound_frame.GetMenuBar().GetMenuCount()

    assert menu_count == 7


# --- demo data flows through the bootstrap (D1 exit criteria) ----


def test_build_main_window_shows_five_demo_feed_rows(
    bound_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Not just a bare MainFrame(): the bootstrap's own console feed."""
    crossings_list = harness.find_control(bound_frame, ids.CROSSINGS_LIST)

    row_count = crossings_list.GetModel().GetCount()

    assert row_count == 5


def test_build_main_window_applies_the_console_canvas_minimum_size(
    bound_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """xrc-windows.md A: "1100x700" -- honoured via the bootstrap."""
    min_size = bound_frame.GetMinSize()

    assert (min_size.width, min_size.height) == (1100, 700)


# --- record-crossing wiring runs at bootstrap (Phase 8, A4) -------


def test_build_main_window_wires_the_console_to_the_running_data_source(
    bound_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """``set_state(data_source.ride_status())`` ran during bootstrap.

    Read-only: ``bound_frame`` never mutates after construction, so
    this shares the fixture with every other assertion in this
    module (fixture docstring).
    """
    plate_input = harness.find_control(bound_frame, ids.PLATE_INPUT)
    status_label = harness.find_control(bound_frame, ids.RIDE_STATUS_LBL)

    assert (plate_input.IsEnabled(), status_label.GetLabelText()) == (True, "RUNNING")


# --- every §15 route is bound (T-3, R-73) -------------------------


@pytest.mark.parametrize("item_id", ALL_ROUTE_ITEM_IDS)
def test_build_main_window_binds_every_route_table_id(
    bound_frame: Any,  # noqa: ANN401 -- wx ships no stubs
    item_id: str,
) -> None:
    """Asserted against commands.ROUTE_TABLE, never a copied list.

    ``Unbind`` reports whether a handler was found (and removes it)
    -- measured the reliable way to prove a binding exists without
    ever invoking it, which matters here since a WINDOW/DIALOG
    route's handler can call ``ShowModal`` and block forever with no
    user present to dismiss it.
    """
    real_id = wx.xrc.XRCID(item_id)

    was_bound = bound_frame.Unbind(wx.EVT_MENU, id=real_id)

    assert was_bound is True


# --- the accelerator table (E1.4.1) -------------------------------


def test_build_main_window_sets_a_valid_accelerator_table(
    bound_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A table was genuinely applied, not left at the frame default."""
    table = bound_frame.GetAcceleratorTable()

    assert table.IsOk() is True


def test_accelerator_entries_returns_exactly_the_menu_bound_rows(
    xrc_resource: object,
) -> None:
    """Enter (no menu_item_id) contributes nothing; the other 3 do."""
    menubar = harness.load_menubar(xrc_resource, ids.MAIN_MENUBAR)

    entries = app_module._accelerator_entries(menubar)

    assert len(entries) == len(MENU_BOUND_ACCELERATORS)


@pytest.mark.parametrize(
    "accelerator", MENU_BOUND_ACCELERATORS, ids=lambda accelerator: accelerator.menu_item_id
)
def test_accelerator_entries_entry_matches_its_own_table_row(
    xrc_resource: object, accelerator: accelerators.Accelerator
) -> None:
    """Each harvested entry's key and command id match its own row."""
    menubar = harness.load_menubar(xrc_resource, ids.MAIN_MENUBAR)
    entries = app_module._accelerator_entries(menubar)
    entries_by_command = {entry.GetCommand(): entry for entry in entries}
    real_id = wx.xrc.XRCID(accelerator.menu_item_id)

    entry = entries_by_command[real_id]

    assert entry.ToString() == accelerator.key


# --- a route says something rather than doing nothing silently ---


def test_command_route_posts_a_not_yet_implemented_status_notice(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A COMMAND row with no engine yet still tells the operator so."""
    route = commands.route_for_id("mi_export_csv")
    _fire_menu_event(firing_frame, "mi_export_csv")

    status_text = firing_frame.GetStatusBar().GetStatusText()

    assert status_text == f"{route.label} — not yet implemented"


def test_unauthored_dialog_route_posts_a_no_window_status_notice(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """The 3 unauthored §15 gaps still say something, not nothing."""
    route = commands.route_for_id("mi_duplicate_ride")
    _fire_menu_event(firing_frame, "mi_duplicate_ride")

    status_text = firing_frame.GetStatusBar().GetStatusText()

    assert status_text == f"{route.label} — no window authored yet"


# --- negative: main() must show the frame before it can block ----


def _decode_probe_output(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """Decode the probe's stdout, or report why it produced nothing.

    The Phase-1 bug this task exists to fix leaves ``main()``
    returning before ``MainLoop`` (and so before the patched hook)
    ever runs at all -- stdout is then empty, which this turns into
    a diagnosable failure dict instead of a bare ``JSONDecodeError``.
    """
    import json  # noqa: PLC0415 -- only this decode step needs it

    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    context = (
        f"returncode={completed.returncode}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    if not lines:
        return {"frame_shown_before_loop": False, "exit_code": None, "context": context}
    result = json.loads(lines[-1])
    result["context"] = context
    return result


def test_main_shows_the_frame_before_entering_the_event_loop() -> None:
    """Negative for the D1 bug: main() must not return before Show().

    Runs in a fresh, spawned interpreter -- never in-process: main()
    builds its own ``wx.App``, and this session's ``wx_app`` fixture
    already has one (console_subprocess_scenarios.py's own reasoning
    for why a second, unbound App must not be built in a shared
    session applies here too).
    """
    completed = subprocess.run(  # noqa: S603 -- sys.executable + a fixed inline probe
        [sys.executable, "-c", _MAINLOOP_PROBE_SCRIPT],
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT_SECONDS,
        check=False,
    )
    result = _decode_probe_output(completed)

    assert (result["frame_shown_before_loop"], result["exit_code"]) == (True, 0), result["context"]
