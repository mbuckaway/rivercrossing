# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for the harness's reference-hygiene seams (Phase 2).

The functional suite's flakiness is a confirmed, open upstream
wxPython/SIP deficiency (EPIC3-SESSION-SUMMARY.md, Addendum 2): SIP's
C++-pointer -> Python-wrapper map retains its entry as long as the
Python wrapper lives, and for C++-constructed objects (XRC controls,
``FindWindowByName`` results) nothing invalidates the entry when the
C++ object dies -- so a wrapper that OUTLIVES its object (held by a
lingering Python reference) poisons later allocations at that address
with the wrong Python class. No in-process repair exists; the
preventive remedies are reference hygiene (drop wrappers so dealloc
evicts the entry) and process freshness.

This module pins the suite-side hygiene seam Phase 2 adds to
``harness``: :func:`harness.close_window` runs ``gc.collect()`` once
a destroyed window's deletion is reaped, breaking reference cycles
between a view and its control wrappers so the wrappers dealloc and
their SIP map entries evict before the next window builds -- the same
explicit collection wxPython's own ``unittests/wtc.py`` ends
``tearDown`` with.

The SIP map itself is C++-side and never observable from Python, so
these tests assert the Python-level precondition -- wrapper
collectability via weakref -- never the map.
"""

import gc
import re
import sys
import weakref
from typing import Any

import harness
import pytest
import wx
import wx.xrc
from _lists_common import demo_seeded_roster

from rivercrossing.ui import ids
from rivercrossing.ui.views.rider_editor import RiderEditor

pytestmark = pytest.mark.functional


@pytest.fixture
def event_frame(wx_app: object) -> Any:  # noqa: ANN401, ARG001 -- ordering only, see docstring
    """Return a plain frame to fire menu events at (no frame churn).

    ``fire_menu_event`` only needs a frame with a bound ``EVT_MENU``
    handler; a bare ``wx.Frame`` exercises the seam without decoding
    the 53-card imagelist a ``main_frame`` build costs.
    """
    frame = wx.Frame(None, title="harness fire_menu_event probe")
    try:
        yield frame
    finally:
        harness.close_window(frame)


# --- close_window's bounded gc.collect (Addendum 2 remedy (a)) ---


def test_close_window_calls_gc_collect_when_the_window_was_destroyed(
    xrc_resource: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A destroyed window's wrapper cycles are collected at teardown."""
    window = harness.load_window(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    calls: list[str] = []
    monkeypatch.setattr(gc, "collect", lambda: calls.append("collected") or 0)

    closed = harness.close_window(window)

    assert (closed, calls) == (True, ["collected"])


def test_close_window_skips_gc_collect_when_the_close_was_vetoed(
    xrc_resource: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound: a vetoed Close() is not a destruction, so no full GC."""
    window = harness.load_window(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    window.Bind(wx.EVT_CLOSE, lambda event: event.Veto())
    calls: list[str] = []
    monkeypatch.setattr(gc, "collect", lambda: calls.append("collected") or 0)

    closed = harness.close_window(window)

    assert (closed, calls) == (False, [])


def test_close_window_collects_a_cycle_between_a_view_and_its_controls(
    xrc_resource: object,
) -> None:
    """RiderEditor's view<->control cycle deallocs once the window dies.

    ``RiderEditor`` binds handlers to its controls with bound methods
    (view -> control -> handler -> view), so dropping the test's own
    reference leaves the view and its control wrappers held by the
    cycle alone; only a collection after the window's destruction can
    dealloc them and evict their SIP map entries. The wrapper must be
    gone for the view/control to be collectable at all -- the
    Python-side precondition for SIP's map entry eviction, which is
    not itself observable from Python.
    """
    window = harness.load_window(xrc_resource, ids.RIDER_EDITOR_DLG, frame=False)
    window.Show()
    window.Layout()
    harness.pump()
    view = RiderEditor(window, roster=demo_seeded_roster())
    view_ref = weakref.ref(view)
    control_ref = weakref.ref(view.riders_list)

    del view
    harness.close_window(window)

    assert (view_ref(), control_ref()) == (None, None)


# --- fire_menu_event's swallowed-traceback release (retention pin) --


def test_fire_menu_event_clears_a_swallowed_handler_exception(
    event_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A raising handler is swallowed by wx; the seam drops sys.last_*.

    wxPython's event dispatch catches a Python exception raised inside
    a handler and calls PyErr_Print, which parks the traceback on
    ``sys`` -- ``sys.last_type``/``sys.last_value``/
    ``sys.last_traceback`` and, on Python 3.11+, ``sys.last_exc``,
    which carries the exception's own ``__traceback__``. The
    traceback holds the failing frame -- and, for app.py's route
    lambdas, the ``_RouteContext`` (frame + roster) its closure keeps
    -- alive for the rest of the process, so the wrapper-cache entries
    for every control that frame touches never evict (the retention
    probe's "Python frames" signature, 2026-08-19). The seam must
    clear that state in a finally, whether or not the handler raised.
    """
    real_id = wx.xrc.XRCID("harness_probe_route")

    def _raising(_event: Any) -> None:  # noqa: ANN401
        raise LookupError("harness probe boom")

    event_frame.Bind(wx.EVT_MENU, _raising, id=real_id)
    sys.last_type = sys.last_value = sys.last_traceback = None
    sys.last_exc = None

    harness.fire_menu_event(event_frame, "harness_probe_route")

    assert (sys.last_type, sys.last_value, sys.last_traceback, sys.last_exc) == (
        None,
        None,
        None,
        None,
    )


def test_fire_menu_event_releases_the_failing_handlers_frame_chain(
    event_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """The swallowed traceback must not keep the handler's refs alive.

    Beyond the ``sys.last_*`` state itself: an object referenced by
    the failing handler's frame must become collectable once the event
    is fired through the seam -- the observable half of "the frame
    chain is released". Without the clear, the traceback's frame
    locals hold the reference and ``gc.collect()`` cannot reach it.

    The marker is reached through *state*, not closed over directly:
    a closure over the marker would hold it for as long as the
    binding lives (``event_frame.Bind`` keeps the handler object), so
    the test clears ``state["marker"]`` before observing -- leaving
    the traceback's frame as the only possible holder.
    """

    class _Marker:
        pass

    state: dict[str, _Marker] = {"marker": _Marker()}
    marker_ref = weakref.ref(state["marker"])
    real_id = wx.xrc.XRCID("harness_probe_route")

    def _raising_holding_marker(_event: Any) -> None:  # noqa: ANN401
        _ = state["marker"]
        raise LookupError("harness probe boom")

    event_frame.Bind(wx.EVT_MENU, _raising_holding_marker, id=real_id)
    sys.last_type = sys.last_value = sys.last_traceback = None

    harness.fire_menu_event(event_frame, "harness_probe_route")

    del state["marker"]
    gc.collect()
    assert marker_ref() is None


# --- Phase 3: wx log capture (Fault B class-2 visibility) ---


def test_recent_wx_log_reports_a_log_line_emitted_during_a_load(
    xrc_resource: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wx.LogError emitted while a window loads appears in the query.

    Phase 3's class-2 diagnostic (Addendum 2): a degraded XRC load
    reports its missing subtree with a one-line ``wxLogError("Creating
    %s failed")`` that the session-wide ``wx.LogStderr()`` target
    (conftest.py) sends to stderr where it is effectively invisible
    mid-run. The harness must capture that line while the load runs and
    expose it through ``recent_wx_log()`` so the failure is diagnosable
    instead of surfacing only as a later stochastic LookupError. The
    line is injected from the resource's own ``LoadDialog`` -- the same
    method XRC's skip machinery logs through -- rather than emitted by
    the test directly, so the capture is proven to span the load itself.
    """
    real_load_dialog = xrc_resource.LoadDialog

    def _load_dialog(parent: Any, name: str) -> Any:  # noqa: ANN401 -- wx
        wx.LogError(f"Creating {name} failed")
        return real_load_dialog(parent, name)

    monkeypatch.setattr(xrc_resource, "LoadDialog", _load_dialog)

    window = harness.load_window(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    harness.close_window(window)

    captured = harness.recent_wx_log()
    assert any(f"Creating {ids.RIDE_LIBRARY_DLG} failed" in line for line in captured)


def test_recent_wx_log_resets_between_loads(
    xrc_resource: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The query reflects only the most recent load, not earlier ones.

    The capture is reset at the start of each load, so a line emitted
    during the first load must not leak into the query after a second
    load that emits nothing. This is the bound that keeps the diagnostic
    cheap and local: ``recent_wx_log()`` always answers "what did the
    last load log?", never "everything the session ever logged".
    """
    real_load_dialog = xrc_resource.LoadDialog
    emitted: list[bool] = []

    def _load_dialog(parent: Any, name: str) -> Any:  # noqa: ANN401 -- wx
        if not emitted:
            wx.LogError("first-load-only marker")
            emitted.append(True)
        return real_load_dialog(parent, name)

    monkeypatch.setattr(xrc_resource, "LoadDialog", _load_dialog)

    first = harness.load_window(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    harness.close_window(first)
    assert any("first-load-only marker" in line for line in harness.recent_wx_log())

    second = harness.load_window(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    harness.close_window(second)

    captured = harness.recent_wx_log()
    assert not any("first-load-only marker" in line for line in captured)


# --- find_control's settle retry + stale-wrapper rejection ---------
# Mirrors ui.views._support.find_control (windows-latest CI): under
# the wx/SIP wrapper-cache corruption, FindWindowByName can miss or
# answer with a stale-typed wrapper for a live control; the bounded
# SafeYield retry settles it, and a wrong-typed wrapper is refused.


def test_find_control_retries_until_the_control_appears(
    xrc_resource: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first-pass miss settles into the real control, not an error."""
    window = harness.load_window(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    real_find = wx.Window.FindWindowByName
    lookups: list[str] = []
    try:
        with monkeypatch.context() as patched:

            def _find(name: str, parent: Any = None) -> Any:  # noqa: ANN401 -- wx
                lookups.append(name)
                if len(lookups) < 3:
                    return None
                return real_find(name, parent)

            patched.setattr(wx.Window, "FindWindowByName", _find)

            control = harness.find_control(window, ids.RIDES_LIST)
            observed = (control.GetName(), len(lookups))
    finally:
        harness.close_window(window)

    assert observed == (ids.RIDES_LIST, 3)


def test_find_control_raises_when_the_control_never_appears(
    xrc_resource: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name that never resolves raises ControlNotFoundError."""
    window = harness.load_window(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    try:
        with monkeypatch.context() as patched:
            patched.setattr(wx.Window, "FindWindowByName", lambda _name, _parent=None: None)

            with pytest.raises(
                harness.ControlNotFoundError,
                match=re.escape(
                    f"window {window.GetName()!r} has no control named 'no_such_control'"
                ),
            ):
                harness.find_control(window, "no_such_control")
    finally:
        harness.close_window(window)


def test_find_control_rejects_a_stale_typed_wrapper(
    xrc_resource: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wrong-typed wrapper is refused, never returned as-is.

    Under the wrapper-cache corruption a lookup can answer with a
    stale wrapper whose Python type is not the live control's;
    accepting it reads the wrong control's state. The harness must
    raise a diagnosable error instead of handing it back.
    """
    window = harness.load_window(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    try:
        with monkeypatch.context() as patched:
            patched.setattr(wx.Window, "FindWindowByName", lambda _name, _parent=None: object())

            with pytest.raises(
                harness.ControlNotFoundError,
                match=re.escape(
                    f"window {window.GetName()!r} resolved {ids.RIDES_LIST!r} to a stale"
                ),
            ):
                harness.find_control(window, ids.RIDES_LIST)
    finally:
        harness.close_window(window)


# --- release_main_window app-level release (F3, SIP poison) ---
# The prompt-free ``main_frame`` cleanup console_subprocess_scenarios
# encodes is now harness.release_main_window's contract (its docstring
# carries the wrapper-cache rationale); these tests pin the observable
# sequence headlessly -- quit flag before the close, the main_frame
# clear, the unbind -- with a fake app, so the follow-up rewiring agent
# can rely on it without a ``main_frame`` build. The real close-and-reap
# half is close_window's own tests plus the subprocess scenario suite.


class _FakeApp:
    """Minimal app double: really_quitting, main_frame and Unbind."""

    def __init__(self, main_frame: object, *, really_quitting: bool = False) -> None:
        self.really_quitting = really_quitting
        self.main_frame = main_frame
        self.unbound: list[object] = []

    def Unbind(self, event_type: object) -> bool:  # noqa: N802 -- mirrors the wx API it fakes
        self.unbound.append(event_type)
        return True


def test_release_main_window_sets_the_quit_flag_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The quit flag is set first, so Close() never prompts."""
    frame = object()
    app = _FakeApp(main_frame=frame)
    order: list[str] = []
    monkeypatch.setattr(
        harness,
        "close_window",
        lambda _window: order.append(f"closed(quitting={app.really_quitting})"),
    )

    harness.release_main_window(app, frame)

    assert (order, app.main_frame) == (["closed(quitting=True)"], None)


def test_release_main_window_clears_the_matching_main_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The matching main_frame is cleared; really_quitting restores."""
    frame = object()
    app = _FakeApp(main_frame=frame)
    monkeypatch.setattr(harness, "close_window", lambda _window: None)

    harness.release_main_window(app, frame)

    # really_quitting returns to its previous value (False here): a
    # leaked True on the session app would make every later close skip
    # its confirm dialog (measured in the 2026-09-07 VM runs).
    assert (app.main_frame, app.really_quitting) == (None, False)


def test_release_main_window_restores_a_previously_true_quitting_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller-set True survives the release unchanged."""
    frame = object()
    app = _FakeApp(main_frame=frame, really_quitting=True)
    monkeypatch.setattr(harness, "close_window", lambda _window: None)

    harness.release_main_window(app, frame)

    assert (app.main_frame, app.really_quitting) == (None, True)


def test_release_main_window_keeps_a_foreign_main_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A main_frame that is not the frame stays for MacReopenApp."""
    closed_frame = object()
    other_frame = object()
    app = _FakeApp(main_frame=other_frame)
    monkeypatch.setattr(harness, "close_window", lambda _window: None)

    harness.release_main_window(app, closed_frame)

    assert (app.main_frame, app.unbound) == (
        other_frame,
        [wx.EVT_QUERY_END_SESSION],
    )


def test_release_main_window_unbinds_the_session_end_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unbind gets the QUERY_END_SESSION binder, nothing else."""
    frame = object()
    app = _FakeApp(main_frame=None)
    monkeypatch.setattr(harness, "close_window", lambda _window: None)

    harness.release_main_window(app, frame)

    assert app.unbound == [wx.EVT_QUERY_END_SESSION]


def test_release_main_window_real_frame_deallocs_after_the_release(
    wx_app: object,  # noqa: ARG001 -- ordering only, see conftest docstring
) -> None:
    """A real frame's wrapper deallocs once release drops every ref.

    The observable half of the SIP-map eviction the contract promises:
    release_main_window destroys the frame and breaks the app-level
    references, so nothing holds the wrapper afterwards and it deallocs
    once the test drops its own reference. A never-shown frame runs the
    same real Destroy()/gc path close_window's own tests use, so no VM
    proof is pending.
    """
    frame = wx.Frame(None, title="harness release probe")
    frame_ref = weakref.ref(frame)
    app = _FakeApp(main_frame=frame)

    harness.release_main_window(app, frame)
    del frame
    gc.collect()

    assert (frame_ref(), app.main_frame, app.really_quitting) == (
        None,
        None,
        False,
    )


# --- Fault-B gate: stale wrappers fail the spec'd-control walk ---
# load_window_verified's completeness gate (F5): under the wx/SIP
# wrapper-cache corruption, FindWindowByName can answer a stale
# wrapper whose Python class is not wx.Window, though the name
# resolves (the address-reuse signature: the type is what is wrong).
# The isinstance check (the same one find_control's settle uses) must
# fail verification, so the degraded load rebuilds instead of passing
# green. RIDE_LIBRARY_DLG is load_window_verified's own fixture window,
# so its spec is proven to resolve on a healthy load.


def test_expected_controls_resolve_true_when_every_spec_control_resolves(
    xrc_resource: object,
) -> None:
    """A healthy RIDE_LIBRARY_DLG passes the completeness walk."""
    window = harness.load_window(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    try:
        resolved = harness._expected_controls_resolve(window, ids.RIDE_LIBRARY_DLG)
    finally:
        harness.close_window(window)
    assert resolved is True


def test_expected_controls_resolve_false_when_every_name_is_stale(
    xrc_resource: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong-typed wrappers fail the walk; the names still resolve."""
    window = harness.load_window(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    try:
        with monkeypatch.context() as patched:
            patched.setattr(wx.Window, "FindWindowByName", lambda _name, _parent=None: object())
            resolved = harness._expected_controls_resolve(window, ids.RIDE_LIBRARY_DLG)
    finally:
        harness.close_window(window)
    assert resolved is False


def test_expected_controls_resolve_false_when_one_late_control_is_stale(
    xrc_resource: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One poisoned name, after healthy ones, fails the whole walk."""
    window = harness.load_window(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    real_find = wx.Window.FindWindowByName
    try:
        with monkeypatch.context() as patched:

            def _find(name: str, parent: object = None) -> object:
                if name == ids.DUPLICATE_BTN:
                    return object()
                return real_find(name, parent)

            patched.setattr(wx.Window, "FindWindowByName", _find)
            resolved = harness._expected_controls_resolve(window, ids.RIDE_LIBRARY_DLG)
    finally:
        harness.close_window(window)
    assert resolved is False


def test_first_missing_control_names_a_stale_control_not_an_empty_name(
    xrc_resource: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Error message names the stale control, never an empty name."""
    window = harness.load_window(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    real_find = wx.Window.FindWindowByName
    try:
        with monkeypatch.context() as patched:

            def _find(name: str, parent: object = None) -> object:
                if name == ids.DUPLICATE_BTN:
                    return object()
                return real_find(name, parent)

            patched.setattr(wx.Window, "FindWindowByName", _find)
            missing = harness._first_missing_control(window, ids.RIDE_LIBRARY_DLG)
    finally:
        harness.close_window(window)
    assert missing == ids.DUPLICATE_BTN


# --- click/type_text require_shown (F5 false-fast guard) ---
# The harness injects synchronously via ProcessEvent, so a drive can
# silently succeed against a hidden control. require_shown=True checks
# IsShown() first and raises instead. The raise and default paths need
# no display (Hide() is a flag change; injection is direct); the shown-
# success paths show a real window and are VM-proof-pending.

_PROBE_FRAME = "require_shown_probe_frame"
_PROBE_BTN = "require_shown_probe_btn"
_PROBE_TEXT = "require_shown_probe_text"


@pytest.fixture
def probe_frame(wx_app: object) -> Any:  # noqa: ANN401, ARG001 -- ordering only
    """Return a never-shown frame carrying the named drive probes."""
    frame = wx.Frame(None, title="harness require_shown probe")
    frame.SetName(_PROBE_FRAME)
    wx.Button(frame, label="Go", name=_PROBE_BTN)
    wx.TextCtrl(frame, value="unchanged", name=_PROBE_TEXT)
    try:
        yield frame
    finally:
        harness.close_window(frame)


def test_click_require_shown_true_hidden_button_raises_naming_it(
    probe_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A hidden button is refused before injection when required."""
    button = harness.find_control(probe_frame, _PROBE_BTN)
    button.Hide()
    fired: list[str] = []

    def _on_click(_event: Any) -> None:  # noqa: ANN401 -- wx event
        fired.append("clicked")

    button.Bind(wx.EVT_BUTTON, _on_click)

    with pytest.raises(
        AssertionError,
        match=re.escape(f"click target {_PROBE_BTN!r} in window {_PROBE_FRAME!r}"),
    ):
        harness.click(probe_frame, _PROBE_BTN, require_shown=True)

    assert fired == []


def test_click_default_still_delivers_to_a_hidden_button(
    probe_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """require_shown defaults False: existing drives stay unchanged."""
    button = harness.find_control(probe_frame, _PROBE_BTN)
    button.Hide()
    fired: list[str] = []

    def _on_click(_event: Any) -> None:  # noqa: ANN401 -- wx event
        fired.append("clicked")

    button.Bind(wx.EVT_BUTTON, _on_click)

    harness.click(probe_frame, _PROBE_BTN)

    assert fired == ["clicked"]


def test_type_text_require_shown_true_hidden_control_raises_naming_it(
    probe_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A hidden text control is refused before SetValue runs."""
    control = harness.find_control(probe_frame, _PROBE_TEXT)
    control.Hide()

    with pytest.raises(
        AssertionError,
        match=re.escape(f"type_text target {_PROBE_TEXT!r} in window {_PROBE_FRAME!r}"),
    ):
        harness.type_text(probe_frame, _PROBE_TEXT, "changed", require_shown=True)

    assert control.GetValue() == "unchanged"


def test_type_text_default_still_sets_a_hidden_control(
    probe_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """require_shown defaults False: existing drives stay unchanged."""
    control = harness.find_control(probe_frame, _PROBE_TEXT)
    control.Hide()

    harness.type_text(probe_frame, _PROBE_TEXT, "changed")

    assert control.GetValue() == "changed"


def test_click_require_shown_true_delivers_when_the_button_is_shown(
    probe_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A shown button passes the guard and the click is delivered.

    VM-proof-pending: the shown path needs a mapped window, which this
    host does not provide. A VM run makes this exact assertion: show
    and lay out the probe frame, pump once, click with require_shown,
    then the bound handler fired and no AssertionError was raised.
    """
    probe_frame.Show()
    probe_frame.Layout()
    harness.pump()
    button = harness.find_control(probe_frame, _PROBE_BTN)
    fired: list[str] = []

    def _on_click(_event: Any) -> None:  # noqa: ANN401 -- wx event
        fired.append("clicked")

    button.Bind(wx.EVT_BUTTON, _on_click)

    harness.click(probe_frame, _PROBE_BTN, require_shown=True)

    assert fired == ["clicked"]


def test_type_text_require_shown_true_delivers_when_control_is_shown(
    probe_frame: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A shown text control passes the guard and SetValue is applied.

    VM-proof-pending: the shown path needs a mapped window, which this
    host does not provide. A VM run makes this exact assertion: show
    and lay out the probe frame, pump once, type with require_shown,
    then the control's value changed and no AssertionError was raised.
    """
    probe_frame.Show()
    probe_frame.Layout()
    harness.pump()
    control = harness.find_control(probe_frame, _PROBE_TEXT)

    harness.type_text(probe_frame, _PROBE_TEXT, "changed", require_shown=True)

    assert control.GetValue() == "changed"


# --- close_window strict (F6 honesty) ---
# Default close_window returns silently when the settle loop ends with
# the name still resolving; strict=True raises WindowStillResolvingError
# instead. The wx boundary is faked below -- a real window cannot be
# kept deliberately unreaped -- and the same FindWindowByName
# monkeypatch idiom the find_control tests use drives the loop.

_CLOSE_HANDLE = 0xC0FFEE
_STRANGER_HANDLE = 0xDEADBEEF


class _FakeClosingWindow:
    """Minimal wx window double for the close loop's fixed queries."""

    def __init__(self, name: str, handle: int) -> None:
        self._name = name
        self._handle = handle

    def GetName(self) -> str:  # noqa: N802 -- mirrors the wx window API it fakes
        return self._name

    def GetHandle(self) -> int:  # noqa: N802 -- mirrors the wx window API it fakes
        return self._handle

    def Close(self) -> bool:  # noqa: N802 -- mirrors the wx window API it fakes
        return True

    def IsBeingDeleted(self) -> bool:  # noqa: N802 -- mirrors the wx window API it fakes
        return True


def test_close_window_strict_true_raises_when_the_name_never_reaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound exhaustion with the same window answering raises."""
    window = _FakeClosingWindow("fault_probe_dlg", _CLOSE_HANDLE)
    same = _FakeClosingWindow("fault_probe_dlg", _CLOSE_HANDLE)
    flushes: list[str] = []
    with monkeypatch.context() as patched:
        patched.setattr(wx.Window, "FindWindowByName", lambda _name, _parent=None: same)
        patched.setattr(harness, "flush_deferred_deletions", lambda: flushes.append("f"))

        with pytest.raises(
            harness.WindowStillResolvingError,
            match=re.escape(
                f"close_window(strict=True): 'fault_probe_dlg' still resolves after "
                f"{harness._CLOSE_SETTLE_ATTEMPTS} settle attempts"
            ),
        ):
            harness.close_window(window, strict=True)

    assert len(flushes) == harness._CLOSE_SETTLE_ATTEMPTS


def test_close_window_strict_true_raises_when_stranger_answers_the_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A different window answering the name raises at once."""
    window = _FakeClosingWindow("fault_probe_dlg", _CLOSE_HANDLE)
    stranger = _FakeClosingWindow("fault_probe_dlg", _STRANGER_HANDLE)
    flushes: list[str] = []
    with monkeypatch.context() as patched:
        patched.setattr(wx.Window, "FindWindowByName", lambda _name, _parent=None: stranger)
        patched.setattr(harness, "flush_deferred_deletions", lambda: flushes.append("f"))

        with pytest.raises(
            harness.WindowStillResolvingError,
            match=re.escape(
                "close_window(strict=True): 'fault_probe_dlg' now resolves to a different window"
            ),
        ):
            harness.close_window(window, strict=True)

    assert flushes == []


def test_close_window_default_returns_when_the_name_never_reaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default stays silent on bound exhaustion -- no raise."""
    window = _FakeClosingWindow("fault_probe_dlg", _CLOSE_HANDLE)
    same = _FakeClosingWindow("fault_probe_dlg", _CLOSE_HANDLE)
    flushes: list[str] = []
    with monkeypatch.context() as patched:
        patched.setattr(wx.Window, "FindWindowByName", lambda _name, _parent=None: same)
        patched.setattr(harness, "flush_deferred_deletions", lambda: flushes.append("f"))

        closed = harness.close_window(window)

    assert (closed, len(flushes)) == (True, harness._CLOSE_SETTLE_ATTEMPTS)


def test_close_window_default_returns_when_stranger_answers_the_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default stays silent when a stranger answers -- no raise."""
    window = _FakeClosingWindow("fault_probe_dlg", _CLOSE_HANDLE)
    stranger = _FakeClosingWindow("fault_probe_dlg", _STRANGER_HANDLE)
    flushes: list[str] = []
    with monkeypatch.context() as patched:
        patched.setattr(wx.Window, "FindWindowByName", lambda _name, _parent=None: stranger)
        patched.setattr(harness, "flush_deferred_deletions", lambda: flushes.append("f"))

        closed = harness.close_window(window)

    assert (closed, flushes) == (True, [])


def test_close_window_strict_true_succeeds_when_the_window_reaps(
    xrc_resource: object,
) -> None:
    """strict=True returns Close()'s value when the window reaps."""
    window = harness.load_window(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)

    closed = harness.close_window(window, strict=True)

    assert closed is True
