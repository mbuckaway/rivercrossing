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

Phase 3 adds the two rider-list pieces both rider lists need:
:class:`RiderRowListModel` (a ``DataViewIndexListModel`` rendering
``RiderRow`` cells through ``ui.rider_columns``) and
:func:`apply_sort_indicator` (the ▲/▼ header marker for the
presenter-owned sort ``riders_list`` and ``console_riders_list``
share).

W10 adds :func:`apply_glass_bezel`: the macOS-26 ``.glass`` bezel
applied to a ``wx.Button`` through its native ``NSButton`` handle.
It is a *native-bezel selection*, not owner-draw -- the button is
already a themed native control and ``setBezelStyle:`` picks the
Liquid Glass material Apple added in macOS 26 -- so R-05's "no
custom-drawn chrome" holds. It is a no-op on Windows and on every
macOS before 26.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import gc
import platform
import sys
from functools import cache
from typing import TYPE_CHECKING, Any

import wx

from rivercrossing.ui.cards_imagelist import CardImageList, load_card_image_list

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rivercrossing.ui.presenters.data_source import RiderRow
    from rivercrossing.ui.rider_columns import RiderColumn

__all__ = [
    "FIND_SETTLE_ATTEMPTS",
    "GLASS_BEZEL_STYLE",
    "RiderRowListModel",
    "apply_glass_bezel",
    "apply_sort_indicator",
    "associate_model",
    "default_card_images",
    "find_control",
]

# See find_control's own docstring for the measured, address-reuse
# stale-lookup hazard this retry bound settles.
FIND_SETTLE_ATTEMPTS = 25

# The two header markers. A marker is a suffix on the column's own
# label, so apply_sort_indicator can strip it back off again -- which
# is what keeps re-marking idempotent (never "Plate ▲ ▼").
_SORT_ASCENDING = " ▲"
_SORT_DESCENDING = " ▼"

_SORT_MARKERS: tuple[str, ...] = (_SORT_ASCENDING, _SORT_DESCENDING)


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


class RiderRowListModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over ``RiderRow`` rows for a rider list.

    Both rider lists draw the same rows and differ only in which
    shared columns they carry (``ui.rider_columns``), so the model
    takes the column list rather than hard-coding one: the editor
    passes :data:`~rivercrossing.ui.rider_columns.EDITOR_RIDER_COLUMNS`
    and the console passes
    :data:`~rivercrossing.ui.rider_columns.CONSOLE_RIDER_COLUMNS`.
    Every cell renders through its column's own ``value`` accessor,
    so a column and the cell it draws cannot drift.

    ``# type: ignore[misc]``: wx ships no stubs, so mypy refuses to
    subclass ``Any`` -- the same unavoidable annotation
    ``CrossingsFeedModel`` carries in ``views/main_frame.py``.
    """

    def __init__(
        self,
        rows: Sequence[RiderRow],
        columns: Sequence[RiderColumn],
    ) -> None:
        """Wrap *rows*, rendering each cell through *columns*."""
        super().__init__(len(rows))
        self._rows = tuple(rows)
        self._columns = tuple(columns)

    def GetColumnCount(self) -> int:
        """Return the number of shared columns this list carries."""
        return len(self._columns)

    def GetColumnType(self, col: int) -> str:  # noqa: ARG002 -- every column is text here
        """Return "string" -- every rider-list column is text."""
        return "string"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the cell value at *row*/*col*."""
        return self._columns[col].value(self._rows[row])


def apply_sort_indicator(
    column_controls: Sequence[Any],
    active_col: int | None,
    *,
    ascending: bool,
) -> None:
    """Mark *active_col*'s header with an arrow, clearing the rest.

    The presenter owns each rider list's row order (a
    ``DataViewIndexListModel`` cannot sort itself), so the header
    marker is written here from the presenter's own state rather than
    by wx: the active column reads ``"Plate ▲"``/``"Plate ▼"`` and
    every other column gets its plain label back. ``None`` *active_col*
    means no sort is active, so every label is plain.

    Idempotent by construction: a marker is stripped from the
    column's current title before the new one is applied, so
    re-marking the same column replaces a marker instead of stacking
    a second one.
    """
    for index, column in enumerate(column_controls):
        title = _plain_label(column.GetTitle())
        if index != active_col:
            column.SetTitle(title)
        elif ascending:
            column.SetTitle(f"{title}{_SORT_ASCENDING}")
        else:
            column.SetTitle(f"{title}{_SORT_DESCENDING}")


def _plain_label(title: str) -> str:
    """Return *title* without a sort marker, if it carries one."""
    for marker in _SORT_MARKERS:
        if title.endswith(marker):
            return title[: -len(marker)]
    return title


# NSBezelStyleGlass is Apple's own enum value (macOS 26.0+), sent
# straight to the native NSButton -- it is not a wx constant.
GLASS_BEZEL_STYLE = 16

# The first macOS with the glass material, from Apple's own versioning.
_GLASS_BEZEL_MAJOR = 26


def apply_glass_bezel(button: wx.Button) -> None:
    """Give *button* the macOS-26 ``.glass`` bezel, where it can apply.

    A native-bezel selection, not owner-draw: the button is already a
    themed native ``NSButton``, and ``setBezelStyle:`` picks the
    Liquid Glass material Apple added in 26 (wxWidgets 3.3.3's Cocoa
    ``wxButton`` supports exactly this access path -- its
    ``button.mm`` handles "application code when accessed with
    ``wxWindow::GetHandle()``").

    A no-op off macOS 26+ and before the button is realized
    (``GetHandle()`` is 0 until the dialog is shown), so Windows, older
    macOS and a not-yet-shown dialog are unaffected. Idempotent --
    setting the same bezel style twice is a no-op.

    Args:
        button: The ``wx.Button`` whose native bezel to change. Call
            this after the dialog is shown (``RideLibrary`` defers it
            through ``wx.CallAfter`` for exactly that reason), or the
            handle is still 0.
    """
    if not _glass_bezel_supported():
        return
    handle = button.GetHandle()
    if not handle:
        return
    _send_set_bezel_style(handle, GLASS_BEZEL_STYLE)


def _glass_bezel_supported() -> bool:
    """Return whether this process can apply the glass bezel.

    Darwin-only and macOS 26+ only: ``NSBezelStyleGlass`` does not
    exist before Tahoe, and neither Windows nor Linux has a native
    ``NSButton`` to set it on.
    """
    if sys.platform != "darwin":
        return False
    return _macos_major() >= _GLASS_BEZEL_MAJOR


def _macos_major() -> int:
    """Return the running macOS major version, or 0 when unparseable.

    ``platform.mac_ver()`` returns ``("26.6.2", ...)`` on Tahoe. An
    empty or non-numeric release (a stripped image, a future format
    change) reads as 0, so the guard fails closed rather than
    guessing.
    """
    release = platform.mac_ver()[0]
    major, _, _ = release.partition(".")
    return int(major) if major.isdigit() else 0


def _send_set_bezel_style(handle: Any, style: int) -> None:  # noqa: ANN401 -- a native pointer
    """Send ``setBezelStyle:`` to the native ``NSButton`` at *handle*.

    The exact recipe this feature's macOS-26 probe pinned: libobjc via
    ``ctypes`` (no new dependency -- PyObjC's ``objc`` + libffi runtime
    would add a real PyInstaller-frozen-app packaging cost),
    ``sel_registerName`` typed to return ``c_void_p``, and
    ``objc_msgSend`` typed ``[c_void_p, c_void_p, c_long]`` with
    restype ``None`` for the void-returning setter.

    Kept apart from :func:`apply_glass_bezel` so the guard and the
    dispatch stay unit-testable without ever dereferencing a pointer:
    a fake handle here would abort the interpreter, so only a real
    realized button on macOS 26 exercises the send itself.

    # logic-coverage-exempt: T-15 -- the send below needs a live
    NSButton pointer and libobjc; unit tests cover the
    ``find_library``-is-None arm and the dispatch above, and the
    user's manual macOS-26 check covers the rest. Both this function
    and its module live outside the coverage gate
    (``pyproject.toml`` omits ``ui/views/*``).
    """
    library = ctypes.util.find_library("objc")
    if library is None:
        return
    objc = ctypes.CDLL(library)
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.objc_msgSend.restype = None
    objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
    selector = objc.sel_registerName(b"setBezelStyle:")
    objc.objc_msgSend(ctypes.c_void_p(handle), selector, style)
