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

import gc
from functools import cache
from typing import Any

import wx

from rivercrossing.ui.cards_imagelist import CardImageList, load_card_image_list

__all__ = ["FIND_SETTLE_ATTEMPTS", "associate_model", "default_card_images", "find_control"]

# See find_control's own docstring for the measured, address-reuse
# stale-lookup hazard this retry bound settles.
FIND_SETTLE_ATTEMPTS = 25


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
    previous top-level window's deletion is pending (or a wrapper is
    otherwise still alive after its C++ object was freed), a freshly-
    allocated control can land at an address the wrapper cache
    still associates with a different, already-destroyed control's
    Python class. Generic methods (``GetName()`` among them) still
    dispatch through the real object's C++ vtable and report
    correctly even then, so name alone does not catch this -- only
    the wrapper's own Python *type* is wrong. Checking
    ``isinstance(control, expected_type)`` is what actually catches
    it, and ``wx.SafeYield()`` -- the same kind of pump
    ``harness.close_window`` uses to flush a deferred deletion --
    resolves it on retry in every case measured at the scale one
    window construction reaches. It is not a complete fix under
    sustained load across a whole test session (a known, reported
    residual risk, not silently swallowed); production never
    approaches that load, since each of these windows is built at
    most once.

    Root cause (confirmed upstream, 2026-08): SIP's C++-pointer ->
    Python-wrapper map retains its entry for as long as the Python
    wrapper lives, and for C++-constructed objects (XRC-loaded
    controls, ``FindWindowByName`` results) nothing notifies SIP when
    the C++ object is destroyed -- so a wrapper that outlives its
    object (a lingering reference, e.g. a retained view or a
    swallowed-exception traceback) poisons every later allocation at
    that address. No released wxPython fixes this (wxWidgets/Phoenix
    #2931, Python-SIP/sip#113, wxWidgets/wxWidgets#26789); the
    remedies are reference hygiene (drop the wrapper so its map entry
    is evicted on dealloc) and process freshness (a fresh process has
    a fresh map).

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
        # The documented remedy for the address-reuse poison is
        # reference hygiene: drop the stale wrapper so its SIP
        # pointer->wrapper entry is evicted on dealloc, THEN
        # re-query. Holding the wrapper across the query (the
        # previous `control = FindWindowByName(...)` shape) kept the
        # stale entry alive during the lookup, so the cache returned
        # the same poison wrapper every attempt.
        del control
        gc.collect()
        control = wx.Window.FindWindowByName(name, window)
        attempts += 1
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
