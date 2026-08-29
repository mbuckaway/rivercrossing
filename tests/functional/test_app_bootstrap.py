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

The route-firing tests that used to live here (E3.2 open-target
defaults, E3.4 Import/Export Riders CSV, the Fault A no-leak pins)
moved to ``test_app_open_target.py`` with their own module-scoped
``firing_frame``: splitting the two heaviest functional files spreads
per-worker window churn across ``--dist loadfile`` workers (the
wrapper-cache corruption remedy).

Two module-scoped fixtures, mirroring ``test_console_demo.py``'s own
``shared_console`` precedent for why sharing matters here: building
``main_frame`` decodes the 53-card imagelist and constructs every
console control, and this wx build's own measured address-reuse
hazard grows with how many windows one session builds and tears down
(``MainFrame._find``'s docstring). ``bound_frame`` serves every
read-only assertion below -- including the Unbind-based route-binding
proof, which removes bindings but touches nothing else that the other
read-only assertions care about. ``firing_frame`` is a second,
independent instance for the two tests that post a real
``EVT_MENU`` event (the one remaining status-notice route, Back Up
Database…, and the Exit confirm flow), kept separate so firing an
event there can never race the binding-removal proof over which
bindings are still present.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

import harness
import pytest
import scenario_runner
import wx
import wx.xrc

from rivercrossing.ui import accelerators, commands, ids
from rivercrossing.ui import app as app_module
from rivercrossing.ui.views import dialogs

if TYPE_CHECKING:
    import subprocess

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
    # force=True: Phase 8's EVT_CLOSE handler vetoes and hides a plain
    # Close() on macOS (P8-D2) instead of destroying the frame, which
    # would leave nothing to end this probe's MainLoop before its own
    # timeout.
    wx.CallLater(200, frame.Close, force=True)
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
    """Post a real ``EVT_MENU`` for *item_id* at *frame*, then settle.

    Delegates to :func:`harness.fire_menu_event`, the shared home of
    this and ``test_app_open_target.py``'s identical helper: it posts
    the event, settles (``flush_deferred_deletions``, bounded), and in
    a ``finally`` clears ``sys.last_*`` so a swallowed handler
    exception's traceback cannot keep its frame chain -- and the
    view/controls it references -- alive for the rest of the process
    (Phase 2 retention pin; ``harness.fire_menu_event``'s docstring).
    """
    harness.fire_menu_event(frame, item_id)


# --- the menubar is attached (T-9) -------------------------------


def test_build_main_window_attaches_a_menubar_with_seven_menus(
    bound_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """R-73/spec §15: File, Ride, Riders, Cards, Results, View, Help."""
    menu_count = bound_frame.GetMenuBar().GetMenuCount()

    assert menu_count == 7


# --- demo data flows through the bootstrap (D1 exit criteria) ----


def test_build_main_window_wires_the_console_to_the_live_engine_feed(
    bound_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Not demo rows: the bootstrap console reads a fresh live engine.

    E4.4.1 swapped the console's ``DemoDataSource`` wiring for an
    ``EngineDataSource`` over a seeded, started ride; a fresh engine
    has no crossings yet, so the feed is empty at startup (the E4.4.4
    mini race drives real rows through this same bootstrap). The
    library/editor/detail windows keep demo data until E5/E6.
    """
    crossings_list = harness.find_control(bound_frame, ids.CROSSINGS_LIST)

    row_count = crossings_list.GetModel().GetCount()

    assert row_count == 0


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

    The bootstrap's seeded ride is started (RUNNING) so the console
    opens live, exactly as the demo source reported RUNNING before
    E4.4.1. Read-only: ``bound_frame`` never mutates after
    construction, so this shares the fixture with every other
    assertion in this module (fixture docstring).
    """
    plate_input = harness.find_control(bound_frame, ids.PLATE_INPUT)
    status_label = harness.find_control(bound_frame, ids.RIDE_STATUS_LBL)

    assert (plate_input.IsEnabled(), status_label.GetLabelText()) == (True, "RUNNING")


# --- theme + zoom menu radio defaults (Phase 8, 8.6, P8-D4) -------


def test_build_main_window_checks_the_theme_and_zoom_menu_defaults(
    bound_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """main.xrc:338-341's documented gap: no ``.Check(`` call existed.

    ``mi_theme_system`` happens to read checked even before this fix,
    since it is the first item of its own ``wxRB_GROUP`` and wx
    checks a fresh radio group's first member by default (measured);
    ``mi_zoom_100`` is not first in its own group and reads
    unchecked until the fix lands, which is what actually turns this
    combined assertion red today.
    """
    menubar = bound_frame.GetMenuBar()

    checked = (
        menubar.IsChecked(wx.xrc.XRCID(ids.MI_THEME_SYSTEM)),
        menubar.IsChecked(wx.xrc.XRCID(ids.MI_ZOOM_100)),
    )

    assert checked == (True, True)


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
    """A COMMAND row with no engine yet still tells the operator so.

    ``mi_backup_now``, not ``mi_export_csv``: E3.4 gave the latter a
    real handler (``_handle_export_csv``), so it no longer exercises
    this generic fallback path at all -- its own dedicated tests live
    alongside the import-CSV ones below.
    """
    route = commands.route_for_id("mi_backup_now")
    _fire_menu_event(firing_frame, "mi_backup_now")

    status_text = firing_frame.GetStatusBar().GetStatusText()

    assert status_text == f"{route.label} — not yet implemented"


def test_void_card_route_targets_the_authored_dialog_not_the_sentinel() -> None:
    """E7 mock-first: the last unauthored §15 gap is now authored.

    E5.4.1 authored Duplicate Ride… and Reopen Ride; E7 authors Void
    Card… as ``void_card_confirm_dlg``, so ``mi_void_card`` targets a
    real dialog instead of the retired ``_UNAUTHORED_DIALOG`` sentinel.
    Its wiring gets its own functional tests in E7.2.1.
    """
    route = commands.route_for_id("mi_void_card")

    assert route.target == ids.VOID_CARD_CONFIRM_DLG


# --- Exit always confirms first (Phase 8, P8-D1) ------------------


def test_exit_route_no_longer_posts_the_not_yet_implemented_stub(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P8-D1: Exit runs the confirm flow, not the generic COMMAND stub.

    ``views.dialogs.run_dialog`` monkeypatched to Cancel -- the STAY
    outcome posts no notice at all -- so the status bar's text before
    and after firing ``wxID_EXIT`` must be identical, and the frame
    must still be alive: proof the special-cased handler ran instead
    of the generic COMMAND stub, which would have overwritten it with
    "Exit — not yet implemented". The replacement's second parameter
    must be named exactly ``opener`` (every call site passes it as
    ``opener=``, ``app.py``'s own ``_confirm_quit``/``_open_target``):
    a ``_opener``-named one silently never runs at all, since wx
    swallows the ``TypeError`` an unmatched keyword raises inside an
    ``EVT_MENU`` handler rather than propagating it here -- measured
    while wiring E3.2's follow-on sweep, and the reason this test's
    own assertion held even while its replacement was never called.
    """
    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: wx.ID_CANCEL)  # noqa: ARG005
    before = firing_frame.GetStatusBar().GetStatusText()

    _fire_menu_event(firing_frame, "wxID_EXIT")

    after = (firing_frame.GetStatusBar().GetStatusText(), firing_frame.IsBeingDeleted())
    assert after == (before, False)


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
    completed = scenario_runner._run_bounded(
        [sys.executable, "-c", _MAINLOOP_PROBE_SCRIPT],
        timeout=_PROBE_TIMEOUT_SECONDS,
    )
    result = _decode_probe_output(completed)

    assert (result["frame_shown_before_loop"], result["exit_code"]) == (True, 0), result["context"]


def test_tick_timer_stops_when_the_frame_is_destroyed(xrc_resource: object) -> None:
    """The 1 s tick timer must not outlive its frame (segfault fix).

    Regression for the measured crash behind the suite's "worker
    crashed" flake: wire_console starts the tick timer; destroying the
    frame left it registered, and the next SafeYield dispatched
    wxTimerImpl::SendEvent against the freed owner (segfault,
    reproduced deterministically in a Tart clone). The fix stops the
    timer on EVT_DESTROY.
    """
    from rivercrossing.ui.presenters.data_source import EmptyDataSource  # noqa: PLC0415
    from rivercrossing.ui.views.main_frame import MainFrame  # noqa: PLC0415

    class _StubPresenter:
        """The presenter surface wire_console/tick touches."""

        def tick(self) -> None:
            """No-op tick: the timer's only call at this scope."""

    window = harness.load_window_verified(xrc_resource, ids.MAIN_FRAME, frame=True)
    console = MainFrame(window, data_source=EmptyDataSource(), resource=xrc_resource)
    console.wire_console(_StubPresenter())  # type: ignore[arg-type]
    try:
        assert console._tick_timer.IsRunning() is True
    finally:
        wx.GetApp().really_quitting = True
        harness.close_window(window)

    assert console._tick_timer.IsRunning() is False
