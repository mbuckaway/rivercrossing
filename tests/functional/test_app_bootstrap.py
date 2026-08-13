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
from pathlib import Path
from typing import Any

import harness
import pytest
import wx
import wx.xrc

from rivercrossing.ui import accelerators, commands, ids
from rivercrossing.ui import app as app_module
from rivercrossing.ui.views import dialogs, rider_editor

pytestmark = pytest.mark.functional

ALL_ROUTE_ITEM_IDS = tuple(item_id for route in commands.ROUTE_TABLE for item_id in route.ids)
MENU_BOUND_ACCELERATORS = tuple(
    accelerator
    for accelerator in accelerators.ACCELERATOR_TABLE
    if accelerator.menu_item_id is not None
)

_PROBE_TIMEOUT_SECONDS = 20

# test_csvio.py's own fixture home (its module docstring) -- reused
# here rather than a tmp_path, so the E3.4 picker-seam tests below
# stay within pytest's own 3-argument budget (CODINGSTANDARDS-
# SIMPLECODE.md:154).
_CLEAN_POOLED_FIXTURE = (
    Path(__file__).resolve().parents[1] / "unit" / "fixtures" / "csv" / "clean_pooled.csv"
)

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


_MENU_EVENT_SETTLE_ATTEMPTS = 10


def _fire_menu_event(frame: Any, item_id: str) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Post a real ``EVT_MENU`` for *item_id* at *frame*, then settle.

    Measured (PR #8's CI, run 31344728049, this suite's own scattered
    residual churn): a route that opens *and* destroys a dialog
    inside this same synchronous call (``mi_import_csv``'s own
    picker -> preview -> commit flow, say) can leave that deletion
    still pending when this returns, racing the very next
    ``_fire_menu_event``'s own window construction. ``harness.
    close_window``'s own deterministic reap does not cover this path:
    production's own ``dialogs.run_dialog``/``_open_target`` destroy
    their windows directly, never through that test-only helper.

    The settle loop calls :func:`harness.flush_deferred_deletions`
    directly rather than ``harness.pump()``: measured on
    windows-latest CI (run 31392502719), driving that flush from
    every single ``harness.pump()`` call in the whole suite -- not
    just here -- turned one functional job's normal ~90s runtime
    into 5h59m28s before the 6-hour cap killed it, so ``harness.
    pump`` (``harness.py``'s own module) no longer flushes on every
    call. This loop's own deletions still need the deterministic
    idle-processing drive ``harness.pump``'s docstring records --
    only a bounded few calls per fired event, not one per pump call
    across the whole suite -- so it keeps calling the flush
    primitive explicitly instead of relying on ``harness.pump`` to
    supply it.
    """
    real_id = wx.xrc.XRCID(item_id)
    event = wx.CommandEvent(wx.EVT_MENU.typeId, real_id)
    event.SetEventObject(frame)
    frame.GetEventHandler().ProcessEvent(event)
    harness.pump()
    for _ in range(_MENU_EVENT_SETTLE_ATTEMPTS):
        harness.flush_deferred_deletions()


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


def test_unauthored_dialog_route_posts_a_no_window_status_notice(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """The 3 unauthored §15 gaps still say something, not nothing."""
    route = commands.route_for_id("mi_duplicate_ride")
    _fire_menu_event(firing_frame, "mi_duplicate_ride")

    status_text = firing_frame.GetStatusBar().GetStatusText()

    assert status_text == f"{route.label} — no window authored yet"


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


# --- E1.5.3 gap closed: the app's own route path applies the ------
# --- recorded default button / initial-focus decisions (E3.2) -----


def _menu_item_id_for_target(target: str) -> str:
    """Return the first ``ROUTE_TABLE`` item id that opens *target*.

    Derived from ``commands.ROUTE_TABLE`` itself rather than a second,
    hand-written mapping, so this can never drift from the one place
    routes are actually declared.
    """
    return next(route.ids[0] for route in commands.ROUTE_TABLE if route.target == target)


@pytest.mark.parametrize("decision", dialogs.DEFAULT_BUTTON_DECISIONS, ids=lambda d: d[0])
def test_open_target_applies_the_recorded_default_button(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
    decision: tuple[str, str],
) -> None:
    """A real menu route applies the default-button decision too.

    Not only a direct ``dialogs.set_default_button`` call
    (test_dialog_behavior.py's own pin on the identical table this
    parametrizes) -- ``dialogs.run_dialog`` is monkeypatched to
    capture ``GetDefaultItem()`` and return immediately, the same
    precedent ``test_exit_route_no_longer_posts_the_not_yet_
    implemented_stub`` uses for the identical reason: ``ShowModal()``
    would otherwise block forever with no user present. By the time
    it runs, the real default is already set -- ``_open_target``'s
    own ``_apply_dialog_defaults`` call for the other three rows,
    ``run_csv_import_flow``'s own identical lookup (``ui.views.
    rider_editor``) for ``csv_preview_dlg`` -- so the captured name
    is a genuine structural fact, not a proxy. ``csv_preview_dlg``'s
    own row needs one more seam: ``_pick_import_path`` monkeypatched
    to a committed fixture, since E3.4 made that dialog's own route
    run a picker before it opens at all -- harmless for the other
    three rows, which never call it.
    """
    dialog_name, control_name = decision
    monkeypatch.setattr(rider_editor, "_pick_import_path", lambda _parent: _CLEAN_POOLED_FIXTURE)
    captured: dict[str, str | None] = {}

    # "opener" (not "_opener"): every call site names it as a keyword
    # (app.py's own module docstring), and a mismatched replacement
    # parameter name raises TypeError at the call boundary that wx
    # silently swallows inside an EVT_MENU handler rather than
    # propagating -- test_exit_route_no_longer_posts_the_not_yet_
    # implemented_stub's own docstring records the same finding.
    def _capture_default(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001
        default_item = dialog.GetDefaultItem()
        captured["name"] = default_item.GetName() if default_item is not None else None
        return wx.ID_CANCEL

    monkeypatch.setattr(dialogs, "run_dialog", _capture_default)

    _fire_menu_event(firing_frame, _menu_item_id_for_target(dialog_name))

    assert captured["name"] == control_name


@pytest.mark.parametrize("decision", dialogs.FORM_FIRST_FIELDS, ids=lambda d: d[0])
def test_open_target_applies_the_recorded_initial_focus(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
    decision: tuple[str, str],
) -> None:
    """A real menu route applies the initial-focus decision too.

    Not only a direct ``dialogs.set_initial_focus`` call
    (test_dialog_behavior.py's own pin on the identical table this
    parametrizes) -- ``set_initial_focus`` is spied with a call-
    through wrapper (the real ``SetFocus()`` still runs) rather than
    probing resulting OS focus, which ``test_dialog_behavior.py``'s
    own module docstring documents as unobservable in this harness
    session.
    """
    dialog_name, field_name = decision
    calls: list[tuple[str, str]] = []
    original_set_initial_focus = dialogs.set_initial_focus

    def _spy_set_initial_focus(dialog: Any, control_name: str) -> None:  # noqa: ANN401
        calls.append((dialog.GetName(), control_name))
        original_set_initial_focus(dialog, control_name)

    monkeypatch.setattr(dialogs, "set_initial_focus", _spy_set_initial_focus)
    # "opener" (not "_opener"): _capture_default's own comment above.
    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: wx.ID_CANCEL)  # noqa: ARG005

    _fire_menu_event(firing_frame, _menu_item_id_for_target(dialog_name))

    assert calls == [(dialog_name, field_name)]


# --- E3.4: File > Import/Export Riders CSV… -----------------------


def test_mi_import_csv_given_a_cancelled_picker_opens_no_window(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """task-briefs.md's own "cancelled picker = no dialog" (E3.4)."""
    monkeypatch.setattr(rider_editor, "_pick_import_path", lambda _parent: None)
    before = len(wx.GetTopLevelWindows())

    _fire_menu_event(firing_frame, "mi_import_csv")

    assert len(wx.GetTopLevelWindows()) == before


def test_mi_import_csv_given_a_picked_path_shows_it_decorated(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Menu -> picker -> csv_preview_dlg opens decorated (E3.4)."""
    monkeypatch.setattr(rider_editor, "_pick_import_path", lambda _parent: _CLEAN_POOLED_FIXTURE)
    captured: dict[str, str] = {}

    def _capture_summary(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001
        captured["summary"] = harness.find_control(dialog, ids.SUMMARY_LBL).GetLabelText()
        return wx.ID_CANCEL

    monkeypatch.setattr(dialogs, "run_dialog", _capture_summary)

    _fire_menu_event(firing_frame, "mi_import_csv")

    assert captured["summary"] == "clean_pooled.csv → 9 riders · 2 teams · 0 conflicts"


def test_mi_import_csv_import_click_commits_into_the_shared_roster(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The committed roster is the same one rider_editor_dlg reads.

    Proven end to end through the app's own two routes, never a
    direct handle on ``_RouteContext.roster``: import clean_pooled.
    csv via ``mi_import_csv`` (clicking wxID_OK for real inside the
    monkeypatched ``run_dialog``), then open ``rider_editor_dlg`` via
    ``mi_rider_editor`` and read its own ``riders_list``.
    """
    monkeypatch.setattr(rider_editor, "_pick_import_path", lambda _parent: _CLEAN_POOLED_FIXTURE)

    def _click_import(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001
        harness.click(dialog, "wxID_OK")
        return wx.ID_OK

    monkeypatch.setattr(dialogs, "run_dialog", _click_import)
    _fire_menu_event(firing_frame, "mi_import_csv")

    captured: dict[str, set[str]] = {}

    def _capture_plates(dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001
        model = harness.find_control(dialog, ids.RIDERS_LIST).GetModel()
        captured["plates"] = {model.GetValueByRow(row, 0) for row in range(model.GetCount())}
        return wx.ID_CANCEL

    monkeypatch.setattr(dialogs, "run_dialog", _capture_plates)
    _fire_menu_event(firing_frame, "mi_rider_editor")

    assert {"1", "2", "3", "4", "10", "11", "12", "20", "21"} <= captured["plates"]


def test_mi_export_csv_given_a_cancelled_picker_is_a_silent_no_op(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled save picker changes nothing, silently (E3.4)."""
    monkeypatch.setattr(rider_editor, "_pick_export_path", lambda _parent: None)
    before = firing_frame.GetStatusBar().GetStatusText()

    _fire_menu_event(firing_frame, "mi_export_csv")

    assert firing_frame.GetStatusBar().GetStatusText() == before


def test_mi_export_csv_given_a_picked_path_writes_the_rosters_own_header(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Menu -> save picker -> csvio.export writes the real file."""
    export_path = tmp_path / "export.csv"
    monkeypatch.setattr(rider_editor, "_pick_export_path", lambda _parent: export_path)

    _fire_menu_event(firing_frame, "mi_export_csv")

    assert export_path.read_text(encoding="utf-8").splitlines()[0] == "plate,name,team_name,notes"


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
