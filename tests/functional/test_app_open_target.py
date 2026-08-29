# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for app.py's open-target route behavior (E1.5.3/E3.2).

Split out of ``test_app_bootstrap.py`` (open-target defaults and the
Fault A no-leak pins) so the heaviest functional files spread their
per-worker window churn across ``--dist loadfile`` workers (the
wrapper-cache corruption remedy; see noxfile.py's own functional-
session docstring for the measurement that motivates it). Everything
here posts a real ``EVT_MENU`` event at one module-scoped
``firing_frame`` and asserts on the window a route opens. Module
fixtures are per-file by design, so this module carries its own
``firing_frame`` fixture; the ``_fire_menu_event`` helper delegates to
the shared ``harness.fire_menu_event`` (which also clears
``sys.last_*`` after a swallowed handler exception, Phase 2).

The File ▸ Import/Export Riders CSV E2E flows lived here until
2026-08-23, when the CSV-preview + rider-editor sequence was split out
into ``test_csv_route_flows.py``: the combined file's ~15 per-process
dialog loads crossed the measured degradation threshold on macOS CI
(PR #9, run 32554309607) and the editor build failed after the preview
loads. This file's remaining loads are the small confirm/form dialogs
the route-defaults proofs parametrize over.
"""

import re
import sys
from pathlib import Path
from typing import Any

import harness
import pytest
import wx
import wx.xrc

from rivercrossing.roster import Roster
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


def _fire_menu_event(frame: Any, item_id: str) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Post a real ``EVT_MENU`` for *item_id* at *frame*, then settle.

    Delegates to :func:`harness.fire_menu_event`, the shared home of
    this and ``test_app_bootstrap.py``'s identical helper: it posts
    the event, settles (``flush_deferred_deletions``, bounded), and in
    a ``finally`` clears ``sys.last_*`` so a swallowed handler
    exception's traceback cannot keep its frame chain -- and the
    view/controls it references -- alive for the rest of the process
    (Phase 2 retention pin; ``harness.fire_menu_event``'s docstring).
    """
    harness.fire_menu_event(frame, item_id)


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


# E7 D3: mi_reassign_plate now requires a selected entry (the dialog
# carries no plate selector, so the route targets "the current entry");
# its no-selection path posts "open an entry first" instead of opening
# the dialog, so this route-path focus pin covers the dialogs that
# still open directly. The reassign dialog's recorded first field is
# still applied by run_reassign when it opens from a selection.
@pytest.mark.parametrize(
    "decision",
    [d for d in dialogs.FORM_FIRST_FIELDS if d[0] != ids.REASSIGN_DLG],
    ids=lambda d: d[0],
)
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
        roster=Roster(),
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
    """A swallowed route-handler exception leaves sys.last_* clear.

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
        firing_frame.Bind(wx.EVT_MENU, app_module._make_route_handler(context, route), id=real_id)

    assert (sys.last_type, sys.last_value, sys.last_traceback, sys.last_exc) == (
        None,
        None,
        None,
        None,
    )
