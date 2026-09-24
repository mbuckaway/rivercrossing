# SPDX-License-Identifier: GPL-3.0-only
"""Shared view-window helpers (SIMPLECODE Rule 3: third real dup).

``_find`` existed near-identically in ``main_frame.py``,
``ride_library.py``, ``rider_editor.py``, ``entry_detail.py`` (since
retired in Phase 2) and ``results_win.py`` -- five copies of "resolve
a control by name inside this window, raising a useful error naming
both the window and the missing control if it is absent."
``_default_card_images``'s process-lifetime cache repeated the same
pattern across ``main_frame.py`` and that same dialog. This module is
their one shared home; every view still exposes ``_find`` as a bound
method (existing tests call it that way), now inherited from
:class:`DialogFindMixin` instead of repeated in each module.

:func:`associate_model` is not a duplication extraction -- see its
own docstring for exactly what it does and does not claim to fix.

:func:`load_dialog` and :func:`load_menubar` answer the same rule,
and both retry a miss through :func:`fresh_resource` -- the one
rebuild source -- up to :data:`_LOAD_ATTEMPTS` times, evicting the
memoized rebuild between attempts so a *frozen degraded* rebuild (the
one that skipped the subtree is exactly the one cached) cannot answer
``None`` for the rest of the session. Every window and menubar load
site -- ``ui/app.py``'s route, quit, self-test and resume flows,
``ui/views/corrections.py``'s six runners, ``crossing_detail.py``,
``ride_library.py``, ``simulator.py`` and the rest of the sites
loading straight off ``wx.xrc.XmlResource.Get()`` -- asked the
resource for its window and got ``None`` back when a load had
silently skipped it. That is the Fault-B degraded-load class, which
reads to the operator as a menu row clicking through to "no window
authored yet".

Phase 3 adds the rider-list piece both rider lists need:
:class:`RiderRowListModel` (a ``DataViewIndexListModel`` rendering
``RiderRow`` cells through ``ui.rider_columns``). Both lists sort
natively -- ``riders_list`` and ``console_riders_list`` append their
columns with the sortable flag and answer wx's header sort through
:meth:`RiderRowListModel.Compare`, exactly as the team editor and the
ride library do. That retired the presenter-owned ▲/▼ marker
(``apply_sort_indicator``): the platform's own header arrow replaces
it.

:func:`append_markup_column` is the one hand-built column the four card
cells need -- ``AppendTextColumn`` cannot enable markup -- see its own
docstring for the three measured details it carries.
"""

from __future__ import annotations

import gc
import logging
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import wx
import wx.xrc  # submodule, not loaded by plain `import wx`

from rivercrossing.ui.cards_imagelist import CardImageList, load_card_image_list

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.ui.presenters.data_source import RiderRow
    from rivercrossing.ui.rider_columns import RiderColumn

__all__ = [
    "FIND_SETTLE_ATTEMPTS",
    "FRAME_SCREEN_MARGIN",
    "XRC_WARN",
    "DialogFindMixin",
    "RiderRowListModel",
    "append_markup_column",
    "associate_model",
    "clamp_to_display",
    "default_card_images",
    "find_control",
    "find_window_by_name",
    "fit_frame_to_screen",
    "fresh_resource",
    "load_dialog",
    "load_menubar",
]

# See find_control's own docstring for the measured, address-reuse
# stale-lookup hazard this retry bound settles.
FIND_SETTLE_ATTEMPTS = 25

# The gap fit_frame_to_screen leaves between the window and the edges
# of the work area (CODINGSTANDARDS-UX-DESKTOP.md section 6: a window
# must never be larger than the display it opens on, and never flush
# against the menu bar, Dock or taskbar).
FRAME_SCREEN_MARGIN = 16


def clamp_to_display(width: int, height: int) -> tuple[int, int]:
    """Clamp a target window size so it never exceeds the work area.

    Use ``wx.GetClientDisplayRect()``, not ``wx.GetDisplaySize()``: the
    former excludes the menu bar and dock. Returns ``(width, height)``,
    never growing the target.
    """
    _x, _y, display_width, display_height = wx.GetClientDisplayRect()
    return (min(width, display_width), min(height, display_height))


# wx ships no stubs
def fit_frame_to_screen(frame: Any, min_size: tuple[int, int]) -> None:  # noqa: ANN401
    """Fit *frame* to the display it is on, and floor it at *min_size*.

    The one cross-platform, OS-branch-free fit (CODINGSTANDARDS-
    UX-DESKTOP.md section 6): the display comes from ``wx.Display.
    GetFromWindow`` (falling back to the primary when wx cannot place
    the window on any display -- ``wx.NOT_FOUND``), and the usable
    rectangle from that display's ``GetClientArea``, which excludes the
    macOS menu bar/Dock and the Windows taskbar on both hosts. Measured
    on wxPython 4.3.1 / wxWidgets 3.3.3: ``GetWorkArea`` does not exist,
    ``GetClientArea`` does.

    The frame's own size is clamped to the work area minus a
    :data:`FRAME_SCREEN_MARGIN` border on each side, and its position is
    clamped so the whole window stays inside the work area -- so a
    geometry persisted on a larger display, or on a display that is no
    longer attached, comes back fully visible. The applied minimum is
    the *smaller* of *min_size* and that area: a floor taller than the
    screen would otherwise force a window larger than the screen.

    Args:
        frame: The ``wx.Frame`` to fit (already sized and positioned --
            this only clamps what is there).
            min_size: The floor the frame would like, as ``(W, H)``.
    """
    index = wx.Display.GetFromWindow(frame)
    if index == wx.NOT_FOUND:
        index = 0
    rect = wx.Display(index).GetClientArea()
    # Floored at zero: a wx.Size is never negative, and a degenerate
    # work area (smaller than the two margins) must not produce one.
    available = (
        max(0, rect.width - 2 * FRAME_SCREEN_MARGIN),
        max(0, rect.height - 2 * FRAME_SCREEN_MARGIN),
    )
    frame.SetMinSize(wx.Size(min(min_size[0], available[0]), min(min_size[1], available[1])))
    size = frame.GetSize()
    width = min(size.width, available[0])
    height = min(size.height, available[1])
    frame.SetSize(wx.Size(width, height))
    position = frame.GetPosition()
    frame.SetPosition(
        wx.Point(
            min(max(position.x, rect.x), rect.x + rect.width - width),
            min(max(position.y, rect.y), rect.y + rect.height - height),
        )
    )


def find_window_by_name(window: Any, name: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return *window*'s descendant control named *name*, or None.

    Recursive ``GetChildren()`` walk -- the scoped, cross-platform
    replacement for ``wx.Window.FindWindowByName(name, window)``, which
    on Windows ARM64 (wxPython 4.3.1) does not resolve children that
    are present. Returns the live child wrapper, so ``isinstance``
    checks against it are correct.
    """
    for child in window.GetChildren():
        if child.GetName() == name:
            return child
        found = find_window_by_name(child, name)
        if found is not None:
            return found
    return None


def find_control(window: Any, name: str, expected_type: type = wx.Window) -> Any:  # noqa: ANN401
    """Resolve one of *window*'s own child controls by name.

    The lookup is :func:`find_window_by_name`'s scoped recursive
    ``GetChildren()`` walk, never ``wx.Window.FindWindowByName``: on
    Windows ARM64 (wxPython 4.3.1) that call does not resolve children
    that are present, so this module resolves them itself, the same way
    on both platforms.

    Callers always pass their own window explicitly as *window*:
    the bare static form of ``FindWindowByName`` defaults to
    searching every top-level window in the process and can resolve
    a same-named control that belongs to a different window
    (``plate_input`` alone exists in four windows).

    Measured under load (many windows built and torn down in one
    process):
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
    it, and ``wx.SafeYield()`` -- the same kind of pump that
    flushes a deferred window deletion -- resolves it on retry in
    every case measured at the scale one window construction
    reaches. It is not a complete fix under sustained load across
    a whole test session (a known, reported residual risk, not
    silently swallowed); production never approaches that load,
    since each of these windows is built at most once.

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
    control = find_window_by_name(window, name)
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
        control = find_window_by_name(window, name)
        attempts += 1
    if not isinstance(control, expected_type):
        children = [child.GetName() for child in window.GetChildren()]
        # LookupError, not TypeError: this is a name that did not
        # resolve inside the window, not a wrong argument type. The
        # child count and names tell a whole-subtree load gap (CI has
        # seen three fresh loads of the same frame each missing a
        # different control) apart from a single genuinely missing
        # name.
        raise LookupError(  # noqa: TRY004
            f"{window.GetName()} has no control named {name!r} "
            f"(first-level children: {len(children)} -- {children!r})"
        )
    return control


class DialogFindMixin:
    """The one ``_find`` every view that decorates an XRC window uses.

    Seventeen views carried a verbatim ``_find`` -- "resolve one of
    this window's own child controls by name, raising with the window
    and the missing control both named" -- differing only in the
    attribute holding the loaded window (``dialog`` everywhere but the
    console, which holds its ``wx.Frame`` in ``frame``). SIMPLECODE
    Rule 3's extraction: the body lives here once and every view
    inherits it.

    The contract is one attribute, :attr:`_window_attr`: the name of
    the instance attribute holding the XRC window this view resolves
    its controls inside. It defaults to ``"dialog"``; ``MainFrame``
    overrides it with ``"frame"``.

    Inheriting keeps :func:`find_control`'s measured address-reuse
    retry (its own docstring) in front of every lookup, and keeps
    ``_find`` a bound method on every view -- the form view code and
    tests call it in.
    """

    # The instance attribute naming the XRC window this view resolves
    # its controls inside.
    _window_attr: ClassVar[str] = "dialog"

    def _find(self, name: str, expected_type: type = wx.Window) -> Any:  # noqa: ANN401
        """Resolve one of this view's own child controls by name.

        See :func:`find_control`'s docstring for the full measured
        reasoning this mirrors: an explicit window parent scopes the
        lookup, and the retry loop settles the address-reuse hazard
        this wx build exhibits under sustained window churn.

        Args:
            name: The frozen control name to resolve.
            expected_type: The wx class the control must be an
                instance of; defaults to any ``wx.Window``.

        Returns:
            The resolved control.

        Raises:
            LookupError: If *name* does not resolve to an
                *expected_type* instance inside the view's window,
                even after settling.
        """
        return find_control(getattr(self, self._window_attr), name, expected_type)


# The packaged XRC directory every view loads from; fresh_resource's
# default source.
_XRC_DIR = Path(__file__).resolve().parent.parent / "xrc"


# The memoized rebuilds, keyed by directory: one parse per directory
# per process.
_rebuilt_resources: dict[Path, Any] = {}


def _log_xrc_warning(message: str) -> None:
    """Record *message*: the launch's log, or the stdlib log.

    The always-on default behind :data:`XRC_WARN`. ``ui.app`` hangs
    the launch's structured log on the app (``app.log``, F1 -- the
    same lookup ``views.dialogs._active_log`` makes), so a running app
    gets the record in its NDJSON file; a construction with no log (a
    bare ``wx.App``, a route-level test, no app at all) still records
    it through stdlib ``logging`` rather than dropping it. Neither path
    raises: a failed write is stdlib logging's own error path
    (``ui.logging``'s docstring), so nothing escapes into the wx
    handler that called us.
    """
    log = getattr(wx.GetApp(), "log", None)
    if log is not None:
        log.warn(message)  # noqa: G010 -- Logging.warn is our own method
        return
    logging.getLogger(__name__).warning(message)


# The always-on record for the XRC load/rebuild failures below, beside
# the wx.LogWarning that only ever reaches stderr: an unreadable .xrc
# or a rebuild that raises is exactly the degraded-load class an
# operator must be able to hand a support session from the log alone.
# A module-level seam, like ``presenters/console.py``'s FINISH_GATE, so
# the app may install its own sink and a test can record the calls.
XRC_WARN: Callable[[str], None] = _log_xrc_warning


def _loaded_xrc_dir(xrc_dir: Path) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return one private ``XmlResource`` loaded from *xrc_dir*.

    Memoized per directory, so a healthy rebuild parses its files once
    per process. A rebuild that failed any load is deliberately *not*
    memoized: the degraded-load class this module exists to work around
    can make the first rebuild the bad one, and a frozen degraded
    resource would defeat every later self-heal.
    """
    cached = _rebuilt_resources.get(xrc_dir)
    if cached is not None:
        return cached
    resource = wx.xrc.XmlResource()
    failed = False
    for path in sorted(xrc_dir.glob("*.xrc")):
        if not resource.Load(str(path)):
            failed = True
            # Load reports an unreadable or partly skipped file only
            # through this boolean, so name the file: a silently
            # missing subtree is the Fault-B degraded-load class this
            # module exists to work around.
            wx.LogWarning(f"XRC load failed for {path}")
            XRC_WARN(f"XRC load failed for {path}")
    if not failed:
        _rebuilt_resources[xrc_dir] = resource
    return resource


def fresh_resource(xrc_dir: Path | None = None) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return a private ``XmlResource`` loaded from every packaged .xrc.

    The rebuild source for the Fault-B degraded-load class: a *new*
    ``wx.xrc.XmlResource``, never the process-wide singleton
    ``wx.xrc.XmlResource.Get`` returns (a parse under load can skip a
    subtree, and a later re-parse can overwrite an earlier clean one),
    loaded from the same ``ui/xrc/*.xrc`` files.

    The built resource is memoized per directory (see
    :func:`_loaded_xrc_dir`), so a healthy rebuild happens once per
    process and a rebuild that failed a load is retried next time.
    *xrc_dir* defaults to the packaged ``ui/xrc``; a test points it at
    a temporary directory to exercise the rebuild without the shipped
    files.
    """
    return _loaded_xrc_dir(xrc_dir if xrc_dir is not None else _XRC_DIR)


# Two fresh attempts per miss. One was not enough: the first attempt can
# land on a memoized *degraded* rebuild -- the rebuild that skipped the
# subtree is exactly the one _loaded_xrc_dir legitimately cached -- and
# every later attempt then reused it, answering None for the rest of the
# session. The loop evicts that cache entry between attempts, so the
# second attempt re-parses from disk.
_LOAD_ATTEMPTS = 2


def load_dialog(
    resource: Any,  # noqa: ANN401 -- wx ships no stubs
    name: str,
    *,
    parent: Any = None,  # noqa: ANN401 -- wx ships no stubs
) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Load dialog *name* from *resource*, rebuilding twice if missing.

    ``LoadDialog`` returns ``None`` when a load skipped the dialog's
    subtree -- the Fault-B class behind the reported ``File ▸
    Simulation…`` "no window authored yet" notice. A miss makes up to
    :data:`_LOAD_ATTEMPTS` fresh attempts through
    :func:`fresh_resource`, evicting the memoized rebuild between them
    so a frozen degraded rebuild cannot answer ``None`` for the rest of
    the session; only a target no ``.xrc`` authors stays ``None``. A
    rebuild that raises (an ``OSError``, a parse error) is reported
    through ``wx.LogWarning`` and :data:`XRC_WARN`, and also answers
    ``None``: this runs inside a wx event handler, where an escaping
    exception is a crash.

    *parent* is passed to both loads. It defaults to ``None`` -- the
    parentless load every menu route uses -- and the two sites that
    parent their dialog (``_load_running_window``,
    ``RideLibrary._on_delete_clicked``) pass their own window: the
    retry must keep the parent, or a self-healed rebuild would
    silently restack the dialog.
    """
    window = resource.LoadDialog(parent, name)
    if window is not None:
        return window
    try:
        for _ in range(_LOAD_ATTEMPTS):
            window = fresh_resource().LoadDialog(parent, name)
            if window is not None:
                return window
            _rebuilt_resources.pop(_XRC_DIR, None)
    except Exception as exc:  # noqa: BLE001 -- a rebuild failure must not reach the wx handler
        wx.LogWarning(f"XRC rebuild failed: {type(exc).__name__}: {exc}")
        XRC_WARN(f"XRC rebuild failed for dialog {name!r}: {type(exc).__name__}: {exc}")
        return None
    return None


def load_menubar(resource: Any, name: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Load menubar *name* from *resource*, rebuilding twice if missing.

    The :func:`load_dialog` shape on the ``LoadMenuBar`` seam: a miss
    makes up to :data:`_LOAD_ATTEMPTS` fresh attempts against
    :func:`fresh_resource`, evicting the memoized rebuild between them
    (the frozen-degraded-rebuild case :func:`load_dialog` documents),
    and a rebuild that raises is reported through ``wx.LogWarning`` and
    :data:`XRC_WARN`, and answers ``None`` rather than escaping into the
    wx handler.
    """
    menubar = resource.LoadMenuBar(None, name)
    if menubar is not None:
        return menubar
    try:
        for _ in range(_LOAD_ATTEMPTS):
            menubar = fresh_resource().LoadMenuBar(None, name)
            if menubar is not None:
                return menubar
            _rebuilt_resources.pop(_XRC_DIR, None)
    except Exception as exc:  # noqa: BLE001 -- a rebuild failure must not reach the wx handler
        wx.LogWarning(f"XRC rebuild failed: {type(exc).__name__}: {exc}")
        XRC_WARN(f"XRC rebuild failed for menubar {name!r}: {type(exc).__name__}: {exc}")
        return None
    return None


@cache
def default_card_images() -> CardImageList:
    """Return the packaged card deck, decoded once per process.

    Shared by every view that draws card bitmaps (``main_frame``,
    the teams editor): there is only ever one console window and one
    editor/dialog open at a time, so neither needs its
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
    ``FindFocus`` elsewhere in this codebase -- so a screen capture
    here cannot actually see the dialog either way, and the
    original observation may be an artifact of that limitation
    rather than a real bug.

    ``Refresh()`` + ``Update()`` right after associating a model is
    nonetheless standard, harmless practice for a macOS
    ``DataViewCtrl`` populated before its first show, so it is
    applied here regardless. This needs confirming on a real,
    interactive desktop before anyone treats it as an actual fix.
    """
    control.AssociateModel(model)
    control.Refresh()
    control.Update()


def append_markup_column(  # noqa: PLR0913 -- mirrors the wx append-column shape
    control: Any,  # noqa: ANN401 -- wx ships no stubs
    label: str,
    col: int,
    *,
    width: int,
    flags: int,
) -> Any:  # noqa: ANN401 -- the appended wx.DataViewColumn
    """Append a text column whose renderer parses **wx markup**.

    The card cells are glyphs, and a card's "suit colour" is a text
    colour -- but a ``DataViewItemAttr`` carries ONE colour per CELL,
    which cannot colour a cell holding several cards of different suits
    (the standings' five-card "Best 5", the console Riders sidebar's
    Cards cell). Such a cell renders markup instead: one
    ``<span color="...">`` per card (``ui.card_text``).

    ``AppendTextColumn`` cannot do that -- it builds its renderer
    internally and takes no renderer argument (measured: wxPython
    4.3.1 exposes no such overload) -- so the column is built by hand as
    ``DataViewColumn(label, renderer, model_col, ...)`` and appended
    with ``AppendColumn``. Three details are load-bearing:

    * ``EnableMarkup()``, or the renderer draws the markup source text;
    * a **fresh** renderer per call: a ``DataViewColumn`` owns its
      renderer, and handing one renderer to two columns aborts the
      process (measured: the second construction segfaults);
    * ``align=wx.ALIGN_NOT``, because ``DataViewColumn`` defaults to
      centred text while ``AppendTextColumn``'s own default is the
      left-aligned ``ALIGN_NOT`` -- the columns beside these are
      left-aligned, so the card cells must be too.

    The width is passed to that constructor exactly as
    ``AppendTextColumn``'s is; a hand-built column resolves it when a
    control takes ownership.
    """
    renderer = wx.dataview.DataViewTextRenderer()
    renderer.EnableMarkup()
    column = wx.dataview.DataViewColumn(
        label, renderer, col, width=width, align=wx.ALIGN_NOT, flags=flags
    )
    return control.AppendColumn(column)


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
