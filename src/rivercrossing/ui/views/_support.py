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

Phase 3 adds the rider-list piece both rider lists need:
:class:`RiderRowListModel` (a ``DataViewIndexListModel`` rendering
``RiderRow`` cells through ``ui.rider_columns``). Both lists sort
natively -- ``riders_list`` and ``console_riders_list`` append their
columns with the sortable flag and answer wx's header sort through
:meth:`RiderRowListModel.Compare`, exactly as the team editor and the
ride library do. That retired the presenter-owned ▲/▼ marker
(``apply_sort_indicator``): the platform's own header arrow replaces
it.
"""

from __future__ import annotations

import gc
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
    "RiderRowListModel",
    "associate_model",
    "default_card_images",
    "find_control",
]

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

    def Compare(  # noqa: PLR0913, PLR0917 -- wx's own four-argument callback shape
        self,
        item1: Any,  # noqa: ANN401 -- wx ships no stubs
        item2: Any,  # noqa: ANN401 -- wx ships no stubs
        col: int,
        ascending: bool,  # noqa: FBT001 -- wx's own callback argument
    ) -> int:
        """Return the Ordering of *item1* versus *item2* on *col*.

        The native header arrows' answer: the control hands this two
        items and the model column, and the comparison runs on the
        *rows* those items index (``DataViewIndexListModel.GetRow``),
        keyed by the list's own column description
        (``ui.rider_columns``), so the editor's list and the console's
        cannot order the same rows differently. The keys are
        heterogeneous between columns (Plate is an ``(int, int)``/
        ``(int, str)`` pair, Sex an ``int``, the rest ``str``), so the
        comparison goes through :func:`_ordering` -- never arithmetic.

        Equal keys fall back to the row's own position, which is
        unique: wx's control-side sort is not stable (unlike the
        presenter's former ``sorted``), so without the tie-break two
        rows showing the same cell could reorder freely between sorts.
        The tie-break is deliberately *not* negated for the downward
        arrow, so equal-key rows keep the presenter's own order in
        both directions. *ascending* is the arrow's own direction.
        """
        first_row = self.GetRow(item1)
        second_row = self.GetRow(item2)
        sort_key = self._columns[col].sort_key
        result = _ordering(sort_key(self._rows[first_row]), sort_key(self._rows[second_row]))
        if result == 0:
            return _ordering(first_row, second_row)
        return result if ascending else -result


def _ordering(first: Any, second: Any) -> int:  # noqa: ANN401 -- the shared column keys' own union
    """Return -1, 0 or 1: how *first* orders against *second*.

    ``Any``, not a TypeVar bound: the rider columns' keys differ in
    type *between* columns (see :meth:`RiderRowListModel.Compare`), so
    no single comparable type covers them all.
    """
    if first == second:
        return 0
    return -1 if first < second else 1
