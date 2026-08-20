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
import weakref

import harness
import pytest
import wx

from rivercrossing.demo import DemoDataSource
from rivercrossing.ui import ids
from rivercrossing.ui.app import _seed_roster
from rivercrossing.ui.views.rider_editor import RiderEditor

pytestmark = pytest.mark.functional


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
