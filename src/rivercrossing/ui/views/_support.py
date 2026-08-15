# SPDX-License-Identifier: GPL-3.0-only
"""Shared view-window helpers (SIMPLECODE Rule 3: third real dup).

``_find`` existed near-identically in ``main_frame.py``,
``ride_library.py``, ``rider_editor.py``, ``entry_detail.py`` and
``results_win.py`` -- five copies of "resolve a control by name
inside this window, raising a useful error naming both the window
and the missing control if it is absent." ``_default_card_images``'s
process-lifetime cache repeated the same pattern across
``main_frame.py`` and ``entry_detail.py``. This module is their one
shared home; every view still exposes its own thin ``_find`` method
(existing tests call it as a bound method) that forwards here.

:func:`associate_model` is not a duplication extraction -- see its
own docstring for exactly what it does and does not claim to fix.
"""

from functools import cache
from typing import Any

import wx

from rivercrossing.ui.cards_imagelist import CardImageList, load_card_image_list

__all__ = [
    "FIND_IDLE_FLUSH_ATTEMPTS",
    "FIND_SETTLE_ATTEMPTS",
    "associate_model",
    "default_card_images",
    "find_control",
]

# See find_control's own docstring for the measured, address-reuse
# stale-lookup hazard this retry bound settles.
FIND_SETTLE_ATTEMPTS = 25

# Escalation bound for find_control's deterministic idle flush, when
# the SafeYield stage cannot drain a deferred deletion under load. 5,
# mirroring tests/functional/harness.py's _FLUSH_IDLE_ATTEMPTS: MSW's
# own UpdateUI idle chatter keeps ProcessIdle() reporting True almost
# every pass, so a larger bound multiplies cost without improving the
# reap (wxAppBase::ProcessIdle calls DeletePendingObjects on the very
# first pass regardless of the bound).
FIND_IDLE_FLUSH_ATTEMPTS = 5


def find_control(window: Any, name: str, expected_type: type = wx.Window) -> Any:  # noqa: ANN401
    """Resolve one of *window*'s own child controls by name.

    Callers always pass their own window explicitly as *window*:
    the bare static form of ``FindWindowByName`` defaults to
    searching every top-level window in the process and can resolve
    a same-named control that belongs to a different window
    (``plate_input`` alone exists in four windows).

    Measured (reproduced under load in this repo's own functional
    suite, many windows built and torn down in one session):
    wxPython wraps wx objects by C++ pointer identity, and when a
    previous window's deletion is still pending, a freshly-
    allocated control can land at an address the wrapper cache
    still associates with a *different*, already-destroyed
    control's Python class. Generic methods (``GetName()`` among
    them) still dispatch through the real object's C++ vtable and
    report correctly even then, so name alone does not catch this
    -- only the wrapper's own Python *type* is wrong. Checking
    ``isinstance(control, expected_type)`` is what actually catches
    it. The retry is two-stage: ``wx.SafeYield()`` first -- the
    same kind of pump ``harness.close_window`` uses to flush a
    deferred deletion -- which resolves every case measured at the
    scale one window construction reaches. Under sustained worker
    load the event queue can starve idle processing, so SafeYield
    alone never completes the pending delete that would clear the
    poisoned cache entry (the load-dependent failure CI hit ~20% of
    loads); a SafeYield-loop miss therefore escalates to
    :func:`_refind_after_idle_flush`, which drives idle processing
    deterministically -- completing ``DeletePendingObjects`` clears
    the cache entry so the re-find re-wraps with the correct type.
    Production never approaches the load that defeats the SafeYield
    stage, since each of these windows is built at most once, but
    the escalation keeps the lookup from failing when it does.

    Raises:
        LookupError: If *name* does not resolve to an
            *expected_type* instance inside *window*, even after
            settling. Names *window*'s own first-level children, so
            a whole-subtree load gap (an ``XmlResource`` degradation)
            reads differently from one missing control.
    """
    control = wx.Window.FindWindowByName(name, window)
    attempts = 0
    while not isinstance(control, expected_type) and attempts < FIND_SETTLE_ATTEMPTS:
        wx.SafeYield()
        control = wx.Window.FindWindowByName(name, window)
        attempts += 1
    if not isinstance(control, expected_type):
        # SafeYield could not drain the deferred deletion (its queue
        # starved idle processing under load); drive idle processing
        # deterministically, then re-find -- only then give up.
        control = _refind_after_idle_flush(window, name)
    if not isinstance(control, expected_type):
        children = [child.GetName() for child in window.GetChildren()]
        # LookupError, not TypeError: mirrors harness.py's own
        # ControlNotFoundError(LookupError) for the identical "name
        # did not resolve inside this window" case. The child count
        # and names tell a whole-subtree load gap (CI has seen three
        # fresh loads of the same frame each missing a different
        # control) apart from a single genuinely missing name.
        raise LookupError(  # noqa: TRY004
            f"{window.GetName()} has no control named {name!r} "
            f"(first-level children: {len(children)} -- {children!r})"
        )
    return control


def _refind_after_idle_flush(window: Any, name: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Drive idle processing deterministically, then re-find *name*.

    The escalation stage of :func:`find_control`. wxWidgets only frees
    a ``Destroy()``ed window during idle processing (``wxApp::
    ScheduleForDestruction`` -> ``DeletePendingObjects``), which is
    exactly what clears the wrapper-cache entry find_control's docstring
    documents as the corruption's root; SafeYield cannot always reach
    idle under load (the measured queue starvation above). Mirrors
    ``tests/functional/harness.py``'s ``flush_deferred_deletions`` --
    production must not import the test harness, so this is its own
    copy of the same machinery: without a running ``MainLoop``, a
    useful ``Yield`` needs a created and activated event loop first.
    Bounded by :data:`FIND_IDLE_FLUSH_ATTEMPTS`, never a sleep.

    Returns:
        Whatever ``wx.Window.FindWindowByName`` resolves after the
        flush -- possibly still the wrong-typed wrapper, which
        :func:`find_control` then reports.
    """
    loop = wx.EventLoopBase.GetActive()
    activator = None
    if loop is None:
        loop = wx.GetApp().GetTraits().CreateEventLoop()
        activator = wx.EventLoopActivator(loop)
    try:
        _drain_idle(loop)
        return wx.Window.FindWindowByName(name, window)
    finally:
        if activator is not None:
            del activator


def _drain_idle(loop: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Yield *loop* once, then process its idle queue until it settles.

    Bounded by :data:`FIND_IDLE_FLUSH_ATTEMPTS`: ``ProcessIdle()``
    reports whether more idle work remains, and a source that never
    settles must not hang the caller. Mirrors ``tests/functional/
    harness.py``'s ``_drain_idle``.
    """
    loop.YieldFor(wx.EVT_CATEGORY_ALL)
    attempts = 0
    while loop.ProcessIdle() and attempts < FIND_IDLE_FLUSH_ATTEMPTS:
        attempts += 1


@cache
def default_card_images() -> CardImageList:
    """Return the packaged card deck, decoded once per process.

    Shared by every view that draws card bitmaps (``main_frame``,
    ``entry_detail``): there is only ever one console window and
    one entry-detail dialog open at a time, so neither needs its
    own separate ``CardImageList`` -- and, measured, repeatedly
    decoding and freeing 53 card bitmaps (once per window
    construction) is what pushes this wx build into the
    address-reuse hazard :func:`find_control` documents, far sooner
    than construction alone does. A caller that genuinely needs an
    isolated imagelist (a test asserting on a deliberately broken
    one, say) still passes ``card_images=`` explicitly; this cache
    only backs the default.
    """
    return load_card_image_list()


def associate_model(control: Any, model: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Associate *model* with *control*, then request a repaint.

    UNVERIFIED remedy, not a confirmed fix. A report claimed a
    ``DataViewCtrl`` whose model was associated before a dialog's
    first ``ShowModal`` did not visibly paint its rows. That could
    not be reproduced as a genuine defect in this environment: a
    terminal-launched process never becomes the macOS foreground
    app, the same limitation that defeats ``UIActionSimulator`` and
    ``FindFocus`` elsewhere in this codebase's own functional suite
    (``harness.py``'s module docstring) -- so a screen capture here
    cannot actually see the dialog either way, and the original
    observation may be an artifact of that limitation rather than a
    real bug.

    ``Refresh()`` + ``Update()`` right after associating a model is
    nonetheless standard, harmless practice for a macOS
    ``DataViewCtrl`` populated before its first show, so it is
    applied here regardless. This needs confirming on a real,
    interactive desktop before anyone treats it as an actual fix.
    """
    control.AssociateModel(model)
    control.Refresh()
    control.Update()
