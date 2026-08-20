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
def event_frame(wx_app: object) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """A plain frame to fire menu events at (no main_frame churn).

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


# --- fire_menu_event's swallowed-traceback release (retention pin) ------


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
