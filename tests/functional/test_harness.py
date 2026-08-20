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
import sys
import weakref
from typing import Any

import harness
import pytest
import wx
import wx.xrc

from rivercrossing.demo import DemoDataSource
from rivercrossing.ui import ids
from rivercrossing.ui.app import _seed_roster
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
    view = RiderEditor(window, roster=_seed_roster(DemoDataSource()))
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
