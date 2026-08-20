# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for app.py's open-target and CSV routes (E3.2/E3.4).

Split out of ``test_app_bootstrap.py`` (open-target defaults, the
Import/Export Riders CSV picker flows, and the Fault A no-leak pins)
so the two heaviest functional files spread their per-worker window
churn across ``--dist loadfile`` workers (the wrapper-cache
corruption remedy; see noxfile.py's own functional-session docstring
for the measurement that motivates it). Everything here posts a real
``EVT_MENU`` event at one module-scoped ``firing_frame`` and asserts
on the window a route opens. Module fixtures are per-file by design,
so this module carries its own ``firing_frame`` fixture and
``_fire_menu_event`` helper rather than importing the originals.
"""

import re
import sys
from pathlib import Path
from typing import Any

import harness
import pytest
import wx
import wx.xrc

from rivercrossing.demo import DemoDataSource
from rivercrossing.ui import app as app_module
from rivercrossing.ui import commands, ids, theme
from rivercrossing.ui.views import dialogs, rider_editor

pytestmark = pytest.mark.functional

# test_csvio.py's own fixture home (its module docstring) -- reused
# here rather than a tmp_path, so the E3.4 picker-seam tests below
# stay within pytest's own 3-argument budget (CODINGSTANDARDS-
# SIMPLECODE.md:154).
_CLEAN_POOLED_FIXTURE = (
    Path(__file__).resolve().parents[1] / "unit" / "fixtures" / "csv" / "clean_pooled.csv"
)


@pytest.fixture(scope="module")
def firing_frame(wx_app: object) -> Any:  # noqa: ANN401 -- ordering only, see docstring
    """Build the one ``main_frame`` every route-firing test shares.

    Every test in this module posts a real ``EVT_MENU`` event at it
    and asserts on the window (or status text) the route opens -- no
    binding-removal proof lives here, so a single instance serves
    them all (``test_app_bootstrap.py``'s own module keeps that
    proof's ``bound_frame`` separate).
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


# ------------------------------- Fault A: the load-construct seam
# (hosted-runner red, deterministic here: _decorate/_apply_dialog_
# defaults and SelfTestDialog construction run between the load and
# the try/finally that destroys the window, and a raise there must
# not leave the just-loaded window fully alive.)


def _make_route_context(
    frame: Any,  # noqa: ANN401 -- wx ships no stubs
    resource: object,
) -> app_module._RouteContext:
    """Build the context ``build_main_window`` threads to routes."""
    return app_module._RouteContext(
        frame=frame,
        resource=resource,
        data_source=DemoDataSource(),
        roster=app_module._seed_roster(DemoDataSource()),
        app=wx.GetApp(),
        theme_controller=theme.ThemeController(wx.GetApp()),
    )


def test_open_target_closes_the_dialog_when_decorate_raises(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
    xrc_resource: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fault A red: a decorate failure must not leak the loaded dialog.

    ``_open_target`` loads *route.target*, then runs ``_decorate``
    (which constructs the code-side view -- ``ui.views._support.
    find_control`` can exhaust its 25 retries and raise a
    ``LookupError`` under hosted-runner load) and ``_apply_dialog_
    defaults`` *before* the ``try/finally`` that destroys the dialog.
    A post-load raise therefore leaks it fully alive
    (``is_being_deleted=False``), is rerun-masked by ``--reruns 2``,
    and later trips the reap pin. ``_decorate`` is forced to raise
    here so the leak is reproduced deterministically: red until
    ``_open_target`` closes the dialog on the way out.
    """
    route = commands.route_for_id(_menu_item_id_for_target(ids.RIDER_EDITOR_DLG))
    context = _make_route_context(firing_frame, xrc_resource)

    def _decorate_that_raises(*_args: Any, **_kwargs: Any) -> None:  # noqa: ANN401
        raise LookupError("simulated decorate failure")

    monkeypatch.setattr(app_module, "_decorate", _decorate_that_raises)

    with pytest.raises(LookupError, match=re.escape("simulated decorate failure")):
        app_module._open_target(context, route)

    # ``Destroy()`` is deferred (measured -- harness.close_window's
    # docstring): reap it before asserting, exactly as the reap pin's
    # own settle does, or the pending deletion still answers
    # ``FindWindowByName``.
    harness.flush_deferred_deletions()
    assert wx.Window.FindWindowByName(ids.RIDER_EDITOR_DLG) is None


def test_run_launch_self_test_closes_the_dialog_when_construction_raises(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
    xrc_resource: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fault A red: SelfTestDialog construction failure must not leak.

    ``_run_launch_self_test`` loads ``selftest_dlg`` and constructs
    ``SelfTestDialog`` (whose ``_find`` -> ``ui.views._support.
    find_control`` can raise under hosted-runner load) *before* the
    ``try/finally`` that destroys the window, so a post-load raise
    leaks it fully alive. ``SelfTestDialog`` is forced to raise here
    so the leak is reproduced deterministically: red until the helper
    closes the dialog on the way out.
    """
    from rivercrossing.ui.views import selftest  # noqa: PLC0415

    context = _make_route_context(firing_frame, xrc_resource)

    def _construction_that_raises(*_args: Any, **_kwargs: Any) -> Any:  # noqa: ANN401
        raise LookupError("simulated selftest construction failure")

    monkeypatch.setattr(selftest, "SelfTestDialog", _construction_that_raises)

    with pytest.raises(LookupError, match=re.escape("simulated selftest construction failure")):
        app_module._run_launch_self_test(context)

    # ``Destroy()`` is deferred (measured -- harness.close_window's
    # docstring): reap it before asserting, exactly as the reap pin's
    # own settle does, or the pending deletion still answers
    # ``FindWindowByName``.
    harness.flush_deferred_deletions()
    assert wx.Window.FindWindowByName(ids.SELFTEST_DLG) is None


# ------------------------------- Phase 2: swallowed-handler traceback
# retention. app.py's own route path binds plain lambdas (harness.fire_
# menu_event is the suite-side seam for firing them); when a handler
# raises, wx swallows it and PyErr_Print parks the traceback on sys,
# holding the lambda's frame -- and the _RouteContext (frame + roster)
# it closes over -- alive for the rest of the process.


def test_route_handler_raise_releases_the_app_route_frame_chain(
    firing_frame: Any,  # noqa: ANN401 -- wx ships no stubs
    xrc_resource: object,
) -> None:
    """A swallowed route-handler exception leaves no sys.last_* traceback.

    ``_make_route_handler`` returns lambdas closing over the
    ``_RouteContext`` (frame + roster + data_source); if such a lambda
    raises, the un-cleared ``sys.last_traceback`` keeps that whole
    chain alive. The binding is swapped to a raising handler, fired
    through the shared seam, then restored so no later test can trip
    the raising seam.
    """
    real_id = wx.xrc.XRCID("mi_backup_now")
    route = commands.route_for_id("mi_backup_now")

    def _raising(_event: Any) -> None:  # noqa: ANN401
        raise LookupError("swallowed route probe boom")

    firing_frame.Unbind(wx.EVT_MENU, id=real_id)
    firing_frame.Bind(wx.EVT_MENU, _raising, id=real_id)
    sys.last_type = sys.last_value = sys.last_traceback = None
    sys.last_exc = None

    try:
        harness.fire_menu_event(firing_frame, "mi_backup_now")
    finally:
        context = _make_route_context(firing_frame, xrc_resource)
        firing_frame.Bind(
            wx.EVT_MENU, app_module._make_route_handler(context, route), id=real_id
        )

    assert (sys.last_type, sys.last_value, sys.last_traceback, sys.last_exc) == (
        None,
        None,
        None,
        None,
    )
