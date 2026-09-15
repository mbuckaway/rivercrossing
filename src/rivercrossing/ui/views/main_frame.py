# SPDX-License-Identifier: GPL-3.0-only
"""``main_frame``: the console (1a), wired to its live DataSource.

xrc-windows.md section A's code-side footnote lists the things
``main.xrc`` cannot express: the crossings feed's DataView columns
and per-row attributes, the card imagelist, the ``main_splitter``
sash restore, per-state menu enabling, and ``SetAppearance``. This
module covers the first three for ``main_frame`` -- per-state menu
enabling is ``commands.py``'s route table (E1.4) and
``SetAppearance`` is ``theme.py``'s job (wired by the app bootstrap,
Phase 8); neither lives here. WS-D/WS-H extend
the same code-side list with the header gauges (two ``RaceClock``
dials and the ``StopLight``, built into main.xrc's placeholder
panels because XRC cannot author a ``wx.Control`` subclass), the
GO/STOP glyphs on the converted bitmap start/stop buttons, and the
review notebook's columns, rows and open-rider seam
(``flagged_list``/``console_riders_list``).

Phase 6 adds three more of the same kind, each one something XRC
cannot express: the frame's screen fit
(:func:`~rivercrossing.ui.views._support.fit_frame_to_screen` -- XRC
cannot know the display a window will open on), the Status box's fixed
width and the Current Lap reading's two-digit width pin (XRC cannot
measure a font), and the ``EVT_DISPLAY_CHANGED`` re-fit.

:class:`MainFrame` decorates an already-XRC-loaded ``wx.Frame`` -- it
never calls ``LoadFrame`` itself. Loading stays the caller's job
(the app bootstrap), matching every other window in this codebase
and the repo's loader rule: reuse the one loader, never build a
second.

**Why no separate ``console_panel.py`` (SIMPLECODE Rule 7 -- one
module until a split earns its keep):** module-skeletons.md names
one for "feed, entry field, counters", but ``main.xrc`` never splits
those controls into their own XRC panel resource -- they are plain
children of this one frame, alongside the splitter and statusbar this
module already owns. Two Python files sharing one XRC
window and one set of ``FindWindowByName`` calls would be a
same-window split with no separable XRC boundary behind it, the
paper-cut kind Rule 7 warns against. If a second real window ever
needs the feed-rendering logic, extract it then.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.dataview
import wx.xrc  # Submodule: plain `import wx` does not load it.

from rivercrossing.ride import RideStatus
from rivercrossing.ui import feed_model, ids, sound, std_dialogs
from rivercrossing.ui.presenters.console import status_text, stop_light_mode
from rivercrossing.ui.presenters.data_source import Counters
from rivercrossing.ui.rider_columns import CONSOLE_RIDER_COLUMNS
from rivercrossing.ui.views import dialogs, team_editor
from rivercrossing.ui.views._support import (
    DialogFindMixin,
    RiderRowListModel,
    _ordering,
    associate_model,
    find_control,
    fit_frame_to_screen,
    load_dialog,
)
from rivercrossing.ui.views.gauges import RaceClock, StopLight, go_bundle, stop_bundle

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date, datetime
    from pathlib import Path

    from rivercrossing.roster import EntryMode
    from rivercrossing.store.backup import HourlyBackup
    from rivercrossing.ui.presenters.console import ConsolePresenter, Cue
    from rivercrossing.ui.presenters.data_source import (
        DataSource,
        FeedRow,
        RiderRow,
    )

__all__ = [
    "CONSOLE_RIDERS_LIST",
    "CROSSINGS_SEARCH",
    "DEFAULT_FEED_SORT",
    "DEFAULT_SASH",
    "EDIT_CROSSING_KEY",
    "ELAPSED_CLOCK",
    "ELAPSED_CLOCK_PANEL",
    "FEED_COLUMN_FLAGS",
    "FLAG_COLUMN_LABELS",
    "FLAG_COL_LAP",
    "FLAG_COL_LAP_TIME",
    "FLAG_COL_PLATE",
    "MAX_CURRENT_LAP",
    "MIN_SIZE",
    "NEEDS_REVIEW_PAGE_LABEL",
    "NO_RIDE_STATUS_TEXT",
    "REMAINING_CLOCK",
    "REMAINING_CLOCK_PANEL",
    "REQUIRED_CONTROLS",
    "REQUIRED_CONTROL_CLASSES",
    "REVIEW_NOTEBOOK",
    "RIDERS_COLUMN_LABELS",
    "RIDERS_COLUMN_WIDTHS",
    "RIDERS_COL_NAME",
    "RIDERS_COL_PLATE",
    "RIDERS_COL_TEAM",
    "RIDERS_LIST_COLUMN_FLAGS",
    "RIDERS_PAGE_LABEL",
    "RIDE_INFO_VALUE_WIDTH",
    "RIDE_LOGO_DISPLAY_SIZE",
    "RIDE_STATUS_LIGHT",
    "RIDE_STATUS_PANEL",
    "START_BLOCKED_COLUMN_LABELS",
    "STATUS_LABEL_WORDS",
    "CrossingsFeedModel",
    "FlaggedListModel",
    "MainFrame",
    "StartBlockedListModel",
]

_TEXT_ACCESSORS: dict[int, Callable[[FeedRow], str]] = {
    feed_model.COL_TIME: lambda row: row.time,
    feed_model.COL_PLATE: lambda row: row.plate,
    feed_model.COL_NAME: feed_model.entry_text,
    feed_model.COL_TEAM: lambda row: row.team,
    feed_model.COL_LAP: feed_model.lap_text,
    feed_model.COL_LAP_TIME: lambda row: row.lap_time,
    feed_model.COL_TOTAL: lambda row: row.total,
}

# The WS-D gauges' frozen names, applied with SetName() because XRC
# cannot author a wx.Control subclass (main.xrc's header). The console
# carries no wxInfoBar at all: the REOPENED banner is status-bar text
# (G4), so nothing sits above the ride-info block.
ELAPSED_CLOCK = "elapsed_clock"
REMAINING_CLOCK = "remaining_clock"
RIDE_STATUS_LIGHT = "ride_status_light"

# Phase 6: the Current Lap reading's own numbers. ``MAX_CURRENT_LAP`` is
# what keeps the two-digit reading two digits, so a third character can
# never widen the pinned column. The reading's colour and font are
# authored in main.xrc (XRC's <fg> and <font> are both honoured on this
# build -- main.xrc's header notes the measurement).
MAX_CURRENT_LAP = 99

# W1: the no-ride console's status label. ``show_no_ride`` sets it
# directly, because a DRAFT reading would tell the operator a ride
# already exists -- the label has to ask for the ride to be created.
# ``_status`` still returns to DRAFT: menu enablement reads the
# presenter seam, never this text.
NO_RIDE_STATUS_TEXT = "CREATE RIDE"

# Phase 6: every word ``ride_status_lbl`` can ever show -- each live
# state's own label, the stopped-RUNNING reading, and the no-ride
# label above -- so the Status column's label is floored at the widest
# of them and a state transition can never resize the column.
STATUS_LABEL_WORDS: tuple[str, ...] = (
    *(status_text(status) for status in RideStatus),
    status_text(RideStatus.RUNNING, stopped=True),
    NO_RIDE_STATUS_TEXT,
)

# §5: the ride-info group's two sizes. main.xrc declares the same 64x64
# logo slot and 240-wide value rows; these are the code-side copies the
# render reads (``_ride_logo_bitmap``'s fit box), recorded here so the
# view never spells a second, drifting pair. The width mirrors the
# authored control width -- every value is filled, never typed into, so
# nothing here resizes them.
RIDE_LOGO_DISPLAY_SIZE = (64, 64)
RIDE_INFO_VALUE_WIDTH = 240

# The five WS-D/WS-H names below ARE XRC-authored (main.xrc), so the
# canonical values live in ui/ids.py (generated by tools/gen_ids.py);
# the module names stay as aliases for the call sites that read them
# as this view's own constants (e.g. REQUIRED_CONTROLS below).
ELAPSED_CLOCK_PANEL = ids.ELAPSED_CLOCK_PANEL
REMAINING_CLOCK_PANEL = ids.REMAINING_CLOCK_PANEL
RIDE_STATUS_PANEL = ids.RIDE_STATUS_PANEL
REVIEW_NOTEBOOK = ids.REVIEW_NOTEBOOK
CONSOLE_RIDERS_LIST = ids.CONSOLE_RIDERS_LIST
CROSSINGS_SEARCH = ids.CROSSINGS_SEARCH

# The feed columns' own flags (Phase 4): every column sorts through
# ``CrossingsFeedModel.Compare`` and resizes. As with
# RIDERS_LIST_COLUMN_FLAGS below, an explicit flags= argument *replaces*
# AppendTextColumn's default rather than OR-ing into it, so both bits
# must be spelled out or macOS sets NSTableColumnNoResizing.
FEED_COLUMN_FLAGS = wx.dataview.DATAVIEW_COL_SORTABLE | wx.dataview.DATAVIEW_COL_RESIZABLE

# The feed's opening sort (Phase 4): Time ascending, so the first row is
# the first crossing of the ride (elapsed 0) and the list reads as the
# ride clock. Re-applied by ``_apply_feed_sort`` after every rebuild;
# an operator's header click replaces it (``_on_feed_column_sorted``).
DEFAULT_FEED_SORT: tuple[int, bool] = (feed_model.COL_TIME, True)

# Phase 6: F2 opens the selected feed row's Crossing Detail. main.xrc's
# menu map has no mi_* row for "edit crossing" (accelerators.
# ACCELERATOR_TABLE's F2 row carries menu_item_id=None, like Enter) and
# XRC's <accel> can only fire a menu item, so the console binds this
# frame-level accelerator in code with its own wx id.
EDIT_CROSSING_KEY = wx.WXK_F2

# The crossings feed's own hotkeys (Phase 7): Delete and Ctrl+D remove
# the selected row through the Crossing Detail delete confirm, Ctrl+E
# retypes its plate through the Plate prompt. Like F2 they are
# code-side frame accelerators -- main.xrc declares no menu item for
# either command. wxPython exposes no WXK_D/WXK_E constants for letter
# keys, so the two Ctrl rows carry the letter's own ordinal, the
# spelling app._accelerator_entries' Ctrl+Z handling already uses.
DELETE_CROSSING_KEY = wx.WXK_DELETE
DELETE_CROSSING_CTRL_KEY = ord("D")
EDIT_PLATE_CROSSING_KEY = ord("E")

# console_riders_list's columns (Phase 4): the console draws every
# shared column (``ui.rider_columns.CONSOLE_RIDER_COLUMNS`` -- Plate |
# Name | Team | Sex | Cards), so the headers, the cells and the sort
# keys are the rider editor's own plus the Cards column, never a
# second copy. The three indexes below name the columns the console's
# own code reads back (``_on_rider_activated`` takes the Plate cell).
RIDERS_COL_PLATE = 0
RIDERS_COL_NAME = 1
RIDERS_COL_TEAM = 2
RIDERS_COLUMN_LABELS: tuple[str, ...] = tuple(column.label for column in CONSOLE_RIDER_COLUMNS)

# AppendTextColumn's own default flags include
# wxDATAVIEW_COL_RESIZABLE, but an explicit flags= argument *replaces*
# the default rather than OR-ing into it -- macOS then sets the
# column NSTableColumnNoResizing -- so both bits must be spelled out
# (team_editor.py's measured note).
RIDERS_LIST_COLUMN_FLAGS = wx.dataview.DATAVIEW_COL_SORTABLE | wx.dataview.DATAVIEW_COL_RESIZABLE

# One width per RIDERS_COLUMN_LABELS entry, in that order. Every
# column defaults to wxDVC_DEFAULT_WIDTH (80 DIP); Name opens at
# double that, the rider editor's own width for it (rider_editor.py's
# COL_NAME_WIDTH).
RIDERS_COLUMN_WIDTHS: tuple[int, ...] = (80, 160, 80, 80, 80)

# flagged_list's columns (WS-H): the three cells of the canvas's
# flagged-row line ("45 · lap 6 · 07:12") as sortable columns, plus
# the Issue column that names why the row entered review.
FLAG_COL_PLATE = 0
FLAG_COL_LAP = 1
FLAG_COL_LAP_TIME = 2
FLAG_COL_ISSUE = 3
FLAG_COLUMN_LABELS: tuple[str, ...] = ("Plate", "Lap", "Lap time", "Issue")

# start_blocked_dlg's one column (Phase 5): the blocked-start issue
# list, one reason per row.
START_BLOCKED_COLUMN_LABELS: tuple[str, ...] = ("Issue",)

# The review notebook's two pages, by label (main.xrc). Phase 4 makes
# the Riders page page 0 -- the notebook's default page -- and leaves
# "Needs Review" at index 1; focus_review_panel looks its page up by
# label through _page_index so a future reorder cannot silently send
# the operator to the wrong tab.
RIDERS_PAGE_LABEL = "Riders"
NEEDS_REVIEW_PAGE_LABEL = "Needs Review"

# The REOPENED corrections banner (spec §3, R-36): the clock stays
# closed and live plate entry stays off; the operator edits, voids or
# adds crossings, then finishes again. set_state posts it to the status
# bar's first field -- no top-of-window InfoBar, so no banner text can
# change the frame's own height (G4).
REOPENED_BANNER = (
    "This ride is open for corrections — entry is locked. "
    "Edit, void, or add crossings, then finish again."
)

# xrc-windows.md section A: "Min frame 1100x700, fits 1366x768."
# W9 raises the floor to 1100x780 (still 1366x768-safe on macOS and
# Windows with a taskbar) so the feed pane opens at a working height.
# Phase 4 retires the 30-row cap that height was originally sized
# around -- crossings_list scrolls the whole ride now, so the number is
# a pane area, not a row count. XRC has no window-level minsize
# property (main.xrc's own header) -- only <size>, which sets the
# *initial* size, not the floor. The frozen canvas drawing still shows
# 1100x700; W15's canvas amendment records the raised size in
# xrc-windows.md.
MIN_SIZE = (1100, 780)

# W9: the fresh-launch splitter position (no persisted sash): the
# canvas draws the review sidebar at ~250 px, so the feed pane takes
# the rest of the 1100-wide frame. E8.1.1's persisted sash always
# wins over this default when present.
DEFAULT_SASH = 850

# How often wire_console's timer drives presenter.tick() (E4.4.1).
# 1 s keeps the clock, counters and R-35's 10 s arm auto-clear honest
# without hammering the DataView with rebuilds.
_TICK_MS = 1000

# How often wire_console's backup timer ticks R-54's hourly scheduler:
# one hour, in the milliseconds wx.Timer.Start takes. The scheduler
# itself decides (from its clock) whether the hour has actually
# advanced, so this interval only has to be no longer than an hour.
_BACKUP_MS = 3_600_000


class CrossingsFeedModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over ``FeedRow`` rows for ``crossings_list``.

    ``# type: ignore[misc]``: wx ships no stubs (pyproject.toml's
    ``ignore_missing_imports`` for ``wx.*``), so every wx member --
    ``DataViewIndexListModel`` included -- resolves to ``Any``, and
    mypy refuses to subclass ``Any``. Unavoidable for the first wx
    base class this codebase subclasses; nothing to fix here.

    The wx-facing half of the crossings feed; ``ui/feed_model.py``
    holds the column layout and the decisions this class delegates to
    (``card_text_or_blank``, ``entry_text``, the flagged-row lookup),
    so those stay testable without ``wx``
    (``tests/unit/ui/test_feed_model.py``). This class has exactly
    one consumer, :class:`MainFrame`, which is why it lives here
    rather than in its own file (SIMPLECODE Rule 7).

    Rows are supplied once at construction -- :class:`MainFrame`
    builds a fresh model each time ``show_feed`` runs rather than
    mutating this one in place, which avoids
    ``DataViewIndexListModel``'s row-count-change notifications
    entirely. Phase 4 retired R-32's 30-row cap, so a long ride's
    model holds every crossing and the list scrolls; the presenter
    only rebuilds it when the ride's own data changed
    (``ConsolePresenter._feed_state``).
    """

    def __init__(self, rows: Sequence[FeedRow]) -> None:
        """Wrap *rows*, newest first."""
        super().__init__(len(rows))
        self._rows = tuple(rows)
        self._flagged = feed_model.flagged_row_indexes(self._rows)
        self._edited = feed_model.edited_row_indexes(self._rows)

    def GetColumnCount(self) -> int:
        """Return the feed's fixed eight columns."""
        return len(feed_model.COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:  # noqa: ARG002 -- the model has one type
        """Return the wx variant type (plain text for every column)."""
        return "string"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the cell value at *row*/*col*."""
        feed_row = self._rows[row]
        if col == feed_model.COL_CARD:
            return feed_model.card_text_or_blank(feed_row.card)
        return _TEXT_ACCESSORS[col](feed_row)

    def Compare(  # noqa: PLR0913, PLR0917 -- wx's own four-argument callback shape
        self,
        item1: Any,  # noqa: ANN401 -- wx ships no stubs
        item2: Any,  # noqa: ANN401 -- wx ships no stubs
        col: int,
        ascending: bool,  # noqa: FBT001 -- wx's own callback argument
    ) -> int:
        """Return the Ordering of *item1* versus *item2* on *col*.

        The native header arrows' answer, exactly as
        :meth:`~rivercrossing.ui.views._support.RiderRowListModel.
        Compare` answers the two rider lists': the comparison runs on
        the *rows* the items index (``DataViewIndexListModel.GetRow``)
        through ``feed_model.COLUMN_SORT_KEYS``, so the feed and the
        riders tab order the same way. The keys are heterogeneous
        between columns (Plate is an ``(int, int)``/``(int, str)``
        pair, three are ``float``, Lap an ``int``, two ``str``), so the
        comparison goes through :func:`_ordering` -- never arithmetic.

        Equal keys fall back to the row's own position, which is
        unique: wx's control-side sort is not stable, so without the
        tie-break two rows showing the same cell could reorder freely
        between sorts. The tie-break is deliberately *not* negated for
        the downward arrow, so equal-key rows keep the feed's own
        (newest-first) order in both directions. *ascending* is the
        arrow's own direction.
        """
        first_row = self.GetRow(item1)
        second_row = self.GetRow(item2)
        sort_key = feed_model.COLUMN_SORT_KEYS[col]
        result = _ordering(sort_key(self._rows[first_row]), sort_key(self._rows[second_row]))
        if result == 0:
            return _ordering(first_row, second_row)
        return result if ascending else -result

    def GetAttrByRow(self, row: int, col: int, attr: Any) -> bool:  # noqa: ANN401, ARG002
        """Bold the whole row when its crossing is flagged or edited.

        The two bold channels share one render: a flagged crossing
        (R-34, a short lap -- held or credited) and an edited crossing
        (E7.2.2, one a correction touched -- spec §3 design 8c's
        "edits highlighted in the feed") both bold the entire row.
        *col* is unused: xrc-windows.md's code-side note bolds the
        whole flagged row, not one cell. A DNF row is *not* a third
        bold channel -- it carries a text marker instead
        (``feed_model.entry_text``), so meaning never rides on weight
        or colour alone.
        """
        if row not in self._flagged and row not in self._edited:
            return False
        attr.SetBold(True)  # noqa: FBT003 -- wx API takes a positional bool
        return True


class FlaggedListModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over flagged ``FeedRow`` rows (WS-H).

    ``# type: ignore[misc]``: the wx-ships-no-stubs note
    :class:`CrossingsFeedModel` carries -- ``DataViewIndexListModel``
    resolves to ``Any`` and mypy refuses to subclass ``Any``.

    The review notebook's "Needs Review" tab: one row per short-lap
    flag -- or duplicate pair -- (the rows the console feed bolds or
    lists), showing Plate | Lap | Lap time | Issue. Rows are supplied
    fresh each ``show_flagged``, exactly like
    :class:`CrossingsFeedModel`'s own rebuild-per-show pattern. The
    Issue column names why each row is here
    (``feed_model.review_issue``).

    ``held_for_row`` is the tab's routing seam (plan §6): the wrapped
    row's ``held`` bit tells the app whether the activation gets a
    confirm/void decision (hold mode, R-34) or opens the credited
    short lap's crossing detail. The four rendered columns do not
    carry it, so it is answered straight off the wrapped row.
    """

    def __init__(self, rows: Sequence[FeedRow]) -> None:
        """Wrap *rows* (the flagged subset, newest first)."""
        super().__init__(len(rows))
        self._rows = tuple(rows)

    def GetColumnCount(self) -> int:
        """Return the flagged list's fixed four columns."""
        return len(FLAG_COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:  # noqa: ARG002 -- the model has one type
        """Return the wx variant type (plain text for every column)."""
        return "string"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the cell value at *row*/*col*."""
        flagged_row = self._rows[row]
        if col == FLAG_COL_PLATE:
            return flagged_row.plate
        if col == FLAG_COL_LAP:
            return str(flagged_row.lap)
        if col == FLAG_COL_ISSUE:
            return feed_model.review_issue(flagged_row)
        return flagged_row.lap_time

    def held_for_row(self, row: int) -> bool:
        """Return whether *row*'s card is held for review (plan §6)."""
        return self._rows[row].held


class StartBlockedListModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over the blocked-start dialog's issue lines.

    ``# type: ignore[misc]``: the wx-ships-no-stubs note
    :class:`CrossingsFeedModel` carries -- ``DataViewIndexListModel``
    resolves to ``Any`` and mypy refuses to subclass ``Any``.

    One "Issue" column and one row per reason
    :attr:`~rivercrossing.ride.StartBlockedError.reasons` reported, in
    that order. Rows are supplied fresh per ``show_start_blocked``,
    the rebuild-per-show pattern the console's other two models use.
    """

    def __init__(self, reasons: Sequence[str]) -> None:
        """Wrap *reasons*, one row each."""
        super().__init__(len(reasons))
        self._reasons = tuple(reasons)

    def GetColumnCount(self) -> int:
        """Return the issue list's single column."""
        return len(START_BLOCKED_COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:  # noqa: ARG002 -- the model has one type
        """Return the wx variant type (plain text)."""
        return "string"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401, ARG002 -- wx ships no stubs; one column
        """Return the blocking issue at *row*."""
        return self._reasons[row]


# Fault-B (degraded-XRC-load) completeness contract: the exact frozen
# control names ``MainFrame.__init__`` resolves via ``_find`` (see the
# ``_find`` calls there). ``ui.app._load_frame_verified`` verifies every
# name in this tuple resolves in a freshly loaded ``main_frame`` and
# rebuilds once from a fresh private ``XmlResource`` if one was skipped.
# This is the single source both sides read, so the guard and
# ``__init__`` cannot silently drift apart -- keep the two in lockstep.
REQUIRED_CONTROLS: tuple[str, ...] = (
    ids.CROSSINGS_LIST,
    CROSSINGS_SEARCH,
    ids.MAIN_SPLITTER,
    ids.PLATE_INPUT,
    ids.RECORD_BTN,
    ids.LAST_CROSSING_LBL,
    # C1/§5: the ride-info group -- the logo, then the six read-only
    # value rows (name/date/venue/organizer/scorer/lap length km).
    ids.RIDE_LOGO_BMP,
    ids.RIDE_NAME_VALUE,
    ids.RIDE_DATE_VALUE,
    ids.RIDE_VENUE_VALUE,
    ids.RIDE_ORGANIZER_VALUE,
    ids.RIDE_SCORER_VALUE,
    ids.RIDE_LAP_KM_VALUE,
    ids.RIDE_STATUS_LBL,
    # Phase 6: the header's Current Lap reading.
    ids.CURRENT_LAP_LBL,
    ids.CROSSINGS_COUNT_LBL,
    ids.CARDS_COUNT_LBL,
    ids.ON_COURSE_LBL,
    ids.SHOE_LBL,
    # W12: the two registration chips beside the four live counters
    # (riders_count_lbl / teams_count_lbl).
    ids.RIDERS_COUNT_LBL,
    ids.TEAMS_COUNT_LBL,
    ids.START_BTN,
    ids.STOP_BTN,
    ids.UNDO_BTN,
    ELAPSED_CLOCK_PANEL,
    REMAINING_CLOCK_PANEL,
    RIDE_STATUS_PANEL,
    ids.CLOCK_ELAPSED_LBL,
    ids.CLOCK_REMAINING_LBL,
    REVIEW_NOTEBOOK,
    ids.FLAGGED_LIST,
    ids.REVIEW_BTN,
    CONSOLE_RIDERS_LIST,
)

# The concrete wx class each REQUIRED_CONTROLS name must resolve to.
# Transcribed verbatim from ``MainFrame.__init__``'s own
# ``self._find(name, Class)`` calls below -- the one name->class
# contract the app guard (``ui.app._load_frame_verified``) reads, so
# the guard can never demand a class the ctor does not. Keep the keys
# in lockstep with REQUIRED_CONTROLS
# (tests/unit/test_main_frame_guard.py pins both transcriptions).
REQUIRED_CONTROL_CLASSES: dict[str, type[wx.Window]] = {
    ids.CROSSINGS_LIST: wx.dataview.DataViewCtrl,
    CROSSINGS_SEARCH: wx.SearchCtrl,
    ids.MAIN_SPLITTER: wx.SplitterWindow,
    ids.PLATE_INPUT: wx.TextCtrl,
    ids.RECORD_BTN: wx.Button,
    ids.LAST_CROSSING_LBL: wx.StaticText,
    ids.RIDE_LOGO_BMP: wx.StaticBitmap,
    ids.RIDE_NAME_VALUE: wx.TextCtrl,
    ids.RIDE_DATE_VALUE: wx.TextCtrl,
    ids.RIDE_VENUE_VALUE: wx.TextCtrl,
    ids.RIDE_ORGANIZER_VALUE: wx.TextCtrl,
    ids.RIDE_SCORER_VALUE: wx.TextCtrl,
    ids.RIDE_LAP_KM_VALUE: wx.TextCtrl,
    ids.RIDE_STATUS_LBL: wx.StaticText,
    ids.CURRENT_LAP_LBL: wx.StaticText,
    ids.CROSSINGS_COUNT_LBL: wx.StaticText,
    ids.CARDS_COUNT_LBL: wx.StaticText,
    ids.ON_COURSE_LBL: wx.StaticText,
    ids.SHOE_LBL: wx.StaticText,
    ids.RIDERS_COUNT_LBL: wx.StaticText,
    ids.TEAMS_COUNT_LBL: wx.StaticText,
    ids.START_BTN: wx.BitmapButton,
    ids.STOP_BTN: wx.BitmapButton,
    ids.UNDO_BTN: wx.Button,
    ELAPSED_CLOCK_PANEL: wx.Panel,
    REMAINING_CLOCK_PANEL: wx.Panel,
    RIDE_STATUS_PANEL: wx.Panel,
    ids.CLOCK_ELAPSED_LBL: wx.StaticText,
    ids.CLOCK_REMAINING_LBL: wx.StaticText,
    REVIEW_NOTEBOOK: wx.Notebook,
    ids.FLAGGED_LIST: wx.dataview.DataViewCtrl,
    ids.REVIEW_BTN: wx.Button,
    CONSOLE_RIDERS_LIST: wx.dataview.DataViewCtrl,
}


def _pin_stop_light(light: StopLight) -> wx.Size:
    """Floor *light* at its own best size and return that size.

    §11: XRC has no window-level minsize and the header row is tight,
    so without an explicit floor the lamp laid out at (0, 0) beside its
    label -- the light was on but invisible. ``DoGetBestSize`` is the
    lamp's own three-circle geometry; caching it as the minimum is what
    keeps the sizer from squeezing it away.
    """
    best = light.DoGetBestSize()
    light.SetMinSize(best)
    return best


def _pin_status_label_width(label: wx.StaticText) -> int:
    """Floor *label* at the widest status word and return that width.

    Phase 6: the Status box has a fixed width, so the label's own text
    must never decide it -- a DRAFT -> REOPENED transition would
    otherwise widen the column and shove the boxes beside it. The floor
    is measured from :data:`STATUS_LABEL_WORDS` (every word
    ``set_state`` and :meth:`show_no_ride` render, ``STOPPED`` and
    :data:`NO_RIDE_STATUS_TEXT` included), in the label's own current
    font, so it follows the platform's metrics.
    """
    width: int = max(label.GetTextExtent(word).width for word in STATUS_LABEL_WORDS)
    label.SetMinSize(wx.Size(width, -1))
    return width


def _pin_current_lap_width(label: wx.StaticText) -> int:
    """Floor *label* at its own two-digit extent and return that width.

    Phase 6: the reading is always two digits (``show_current_lap``
    caps at :data:`MAX_CURRENT_LAP`), so pinning the label's width to
    that extent keeps the Current Lap box a fixed size -- the largest
    text it can ever show is what it is measured for, in its own
    2x-relative font.
    """
    width: int = label.GetTextExtent(f"{MAX_CURRENT_LAP:02d}").width
    label.SetMinSize(wx.Size(width, -1))
    return width


class MainFrame(DialogFindMixin):  # _find: ui.views._support, over self.frame
    """Code-side behaviour for ``main_frame`` (the console, 1a).

    Implements ``ConsoleView`` (module-skeletons.md's presenter
    contract). E4.4.1-E4.4.3 grew the Protocol with the four members
    the live presenter actually calls (``set_stop_enabled``,
    ``set_time_columns``, ``show_clock``, ``set_entry_locked``) -- the
    "add the member once the presenter calls it" precedent this
    class's own earlier docstring recorded for ``set_time_columns``.
    :meth:`wire_console` binds the lifecycle controls (start/stop/
    undo) and the tick timer, mirroring :meth:`wire_entry`'s
    callback idiom; the app bootstrap calls it after construction.
    """

    def __init__(  # noqa: PLR0913, PLR0915 -- constructor: (frame, data_source) + E4.4.2/E8.1.1 seams + every control it resolves
        self,
        frame: wx.Frame,
        *,
        data_source: DataSource,
        initial_sash: int | None = None,
        initial_geometry: tuple[int, int, int, int] | None = None,
        on_layout_changed: Callable[[int | None, tuple[int, int, int, int] | None], None]
        | None = None,
        backup_scheduler: HourlyBackup | None = None,
    ) -> None:
        """Decorate an already-loaded ``main_frame`` window.

        Args:
            frame: The ``wx.Frame`` the caller already loaded
                from ``main.xrc``.
            data_source: The display-data seam (module-skeletons.md
                ``ui.presenters``). This view knows only the
                :class:`~rivercrossing.ui.presenters.data_source.
                DataSource` Protocol -- the caller wires in whichever
                implementation applies (``EngineDataSource`` from
                E4.4.1, or ``EmptyDataSource``).
            initial_sash: The persisted splitter sash position to
                restore at construction (E8.1.1); ``None`` keeps the
                XRC default.
            initial_geometry: The persisted frame placement
                ``(x, y, width, height)`` to restore at construction
                (E8.1.1); ``None`` keeps the canvas default size.
            on_layout_changed: Callback ``(sash, geometry)`` the app
                wires to the settings store; fired on sash change,
                move/resize and close. ``None`` (test constructions)
                reports nothing.
            backup_scheduler: R-54's hourly automatic backup
                (``store.backup.schedule_hourly``), whose ``tick()``
                :meth:`wire_console` drives from this frame's own
                timer. ``None`` (a no-store bootstrap, or a test
                construction) wires no backup timer at all.
        """
        self.frame = frame
        self.data_source = data_source
        self._on_layout_changed = on_layout_changed
        self._backup_scheduler = backup_scheduler

        # Every name resolved below is also in module-level
        # REQUIRED_CONTROLS, the Fault-B completeness contract
        # ui.app._load_frame_verified verifies before this constructor
        # runs. Keep the two in lockstep.
        self.crossings_list = self._find(ids.CROSSINGS_LIST, wx.dataview.DataViewCtrl)
        # Phase 4: the search row main.xrc declares above the list (the
        # rider editor's own label + wxSearchCtrl pair).
        self.crossings_search = self._find(CROSSINGS_SEARCH, wx.SearchCtrl)
        self.main_splitter = self._find(ids.MAIN_SPLITTER, wx.SplitterWindow)
        self.plate_input = self._find(ids.PLATE_INPUT, wx.TextCtrl)
        self.record_btn = self._find(ids.RECORD_BTN, wx.Button)
        self.last_crossing_lbl = self._find(ids.LAST_CROSSING_LBL, wx.StaticText)
        # C1/§5: the ride-info group -- the ride's own logo and the six
        # read-only value rows show_ride_header fills.
        self.ride_logo_bmp = self._find(ids.RIDE_LOGO_BMP, wx.StaticBitmap)
        self.ride_name_value = self._find(ids.RIDE_NAME_VALUE, wx.TextCtrl)
        self.ride_date_value = self._find(ids.RIDE_DATE_VALUE, wx.TextCtrl)
        self.ride_venue_value = self._find(ids.RIDE_VENUE_VALUE, wx.TextCtrl)
        self.ride_organizer_value = self._find(ids.RIDE_ORGANIZER_VALUE, wx.TextCtrl)
        self.ride_scorer_value = self._find(ids.RIDE_SCORER_VALUE, wx.TextCtrl)
        self.ride_lap_km_value = self._find(ids.RIDE_LAP_KM_VALUE, wx.TextCtrl)
        self.ride_status_lbl = self._find(ids.RIDE_STATUS_LBL, wx.StaticText)
        self.current_lap_lbl = self._find(ids.CURRENT_LAP_LBL, wx.StaticText)
        self.crossings_count_lbl = self._find(ids.CROSSINGS_COUNT_LBL, wx.StaticText)
        self.cards_count_lbl = self._find(ids.CARDS_COUNT_LBL, wx.StaticText)
        self.on_course_lbl = self._find(ids.ON_COURSE_LBL, wx.StaticText)
        self.shoe_lbl = self._find(ids.SHOE_LBL, wx.StaticText)
        # W12: the two registration chips (riders_count_lbl /
        # teams_count_lbl), resolved like the four live counters.
        self.riders_count_lbl = self._find(ids.RIDERS_COUNT_LBL, wx.StaticText)
        self.teams_count_lbl = self._find(ids.TEAMS_COUNT_LBL, wx.StaticText)

        # E4.4.1 lifecycle controls (start/stop/undo + clock).
        self.start_btn = self._find(ids.START_BTN, wx.BitmapButton)
        self.stop_btn = self._find(ids.STOP_BTN, wx.BitmapButton)
        self.undo_btn = self._find(ids.UNDO_BTN, wx.Button)
        # WS-D gauge slots: the dials and the status lamp are built
        # code-side inside main.xrc's placeholder panels (XRC cannot
        # author a wx.Control subclass -- its header footnote).
        self.elapsed_clock_panel = self._find(ELAPSED_CLOCK_PANEL, wx.Panel)
        self.remaining_clock_panel = self._find(REMAINING_CLOCK_PANEL, wx.Panel)
        self.ride_status_panel = self._find(RIDE_STATUS_PANEL, wx.Panel)
        self.clock_elapsed_lbl = self._find(ids.CLOCK_ELAPSED_LBL, wx.StaticText)
        self.clock_remaining_lbl = self._find(ids.CLOCK_REMAINING_LBL, wx.StaticText)
        # Reserve a fixed width for a full "H:MM:SS" timestamp so a
        # growing elapsed/remaining label can never reflow its dial
        # column (XRC has no window minsize; measured overlap on macOS
        # at default size predates the dial columns -- R-55).
        clock_width = self.clock_elapsed_lbl.GetTextExtent("23:59:59").width
        self.clock_elapsed_lbl.SetMinSize(wx.Size(clock_width, -1))
        self.clock_remaining_lbl.SetMinSize(wx.Size(clock_width, -1))
        # Phase 6: the Current Lap reading's width is pinned to its own
        # two-digit extent -- the reading is capped at MAX_CURRENT_LAP,
        # so nothing wider can ever be rendered into it.
        _pin_current_lap_width(self.current_lap_lbl)
        # Phase 6: and the Status box's label is floored at the widest
        # status word, so its fixed column never resizes on a state
        # change.
        _pin_status_label_width(self.ride_status_lbl)
        self.elapsed_clock = self._build_clock_dial(self.elapsed_clock_panel, ELAPSED_CLOCK)
        self.remaining_clock = self._build_clock_dial(self.remaining_clock_panel, REMAINING_CLOCK)
        self.ride_status_light = StopLight(self.ride_status_panel)
        self.ride_status_light.SetName(RIDE_STATUS_LIGHT)
        # §11: reserve the lamp's own best size before inserting it --
        # the leading header column squeezes it to nothing otherwise.
        _pin_stop_light(self.ride_status_light)
        self.ride_status_panel.GetSizer().Insert(
            0, self.ride_status_light, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 8
        )
        # WS-D GO/STOP glyphs. The labels are re-applied because the
        # XRC bitmap-button handler ignores <label> text (measured);
        # without them the buttons would be icon-only and unnamed to
        # assistive tech (UX-DESKTOP section 7).
        self.start_btn.SetBitmap(go_bundle().GetBitmapFor(self.start_btn))
        self.start_btn.SetLabel("Start ride")
        self.stop_btn.SetBitmap(stop_bundle().GetBitmapFor(self.stop_btn))
        self.stop_btn.SetLabel("Stop ride…")
        # C2: Stop is disabled at rest even if the XRC ever leaves it
        # enabled; the presenter's refresh_console_gates (called from
        # every set_state/show_feed render) is the real source, this
        # just avoids a flash before the first render.
        self.stop_btn.Enable(False)  # noqa: FBT003 -- wx API takes a positional bool

        # WS-H review notebook: the two DataView shells, their
        # columns, and the review_btn/open-rider wiring (both are pure
        # view seams; the app wires open-entry-detail later).
        self.review_notebook = self._find(REVIEW_NOTEBOOK, wx.Notebook)
        self.flagged_list = self._find(ids.FLAGGED_LIST, wx.dataview.DataViewCtrl)
        self.review_btn = self._find(ids.REVIEW_BTN, wx.Button)
        self.console_riders_list = self._find(CONSOLE_RIDERS_LIST, wx.dataview.DataViewCtrl)
        self._flagged_columns = self._build_flagged_columns()
        self._riders_columns = self._build_riders_columns()
        self._flagged_model: FlaggedListModel | None = None
        self._riders_model: RiderRowListModel | None = None
        # The operator's current riders-tab header sort, re-applied
        # whenever the model is rebuilt (a new model drops the
        # control's sort key). No column sorts until a header click.
        self._riders_sort_column: int | None = None
        self._riders_sort_ascending = True
        # Phase 5: the blocked-start dialog's model, alive only while
        # that modal is up (show_start_blocked).
        self._start_blocked_model: StartBlockedListModel | None = None
        self._on_open_rider: Callable[[str], None] | None = None
        # W11 F2a: the flagged tab's activation seam (its own slot --
        # activating a flagged row routes to the review decision or
        # the credited lap's crossing detail, not the rider editor, so
        # the two lists keep separate callbacks). It fires
        # ``(plate, held)``: the row's own disposition decides.
        self._on_open_flagged: Callable[[str, bool], None] | None = None
        # J2: the crossings feed's activation seam. Its own slot too:
        # the feed is keyed by row index (the app resolves that back to
        # the live Crossing), not by plate like the other two lists.
        self._on_open_crossing: Callable[[int], None] | None = None
        # Phase 7: the feed's two hotkey seams, resolved the same way.
        # Delete/Ctrl+D removes the selected row through the Crossing
        # Detail delete confirm; Ctrl+E retypes its plate through the
        # Plate prompt. Separate slots because the app runs a
        # different flow for each.
        self._on_delete_crossing: Callable[[int], None] | None = None
        self._on_edit_plate_crossing: Callable[[int], None] | None = None
        self.review_btn.Bind(wx.EVT_BUTTON, lambda _event: self._on_review_clicked())
        self.crossings_list.Bind(
            wx.dataview.EVT_DATAVIEW_ITEM_ACTIVATED, self._on_crossing_activated
        )
        # Phase 4: the feed's own header sort (every column is
        # sortable) and the search box above it. Both are view-local
        # state; the search text is forwarded to the presenter, which
        # owns the filter.
        self.crossings_list.Bind(
            wx.dataview.EVT_DATAVIEW_COLUMN_SORTED,
            self._on_feed_column_sorted,
        )
        self.crossings_search.Bind(wx.EVT_TEXT, self._on_search_text)
        self.crossings_search.Bind(wx.EVT_SEARCHCTRL_SEARCH_BTN, self._on_search_text)
        self.crossings_search.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_search_text)
        self.console_riders_list.Bind(
            wx.dataview.EVT_DATAVIEW_ITEM_ACTIVATED, self._on_rider_activated
        )
        # Remember the operator's riders-tab header arrow, so the next
        # show_riders rebuild can put it back.
        self.console_riders_list.Bind(
            wx.dataview.EVT_DATAVIEW_COLUMN_SORTED,
            self._on_column_sorted,
        )
        self.flagged_list.Bind(wx.dataview.EVT_DATAVIEW_ITEM_ACTIVATED, self._on_flagged_activated)

        # LoadFrame does not honour main.xrc's <size> -- measured: the
        # frame comes back sized to the sizer's own computed minimum
        # (~429x373), not the canvas's 1100x700. SetSize is what
        # actually grows this window (and the splitter's client area)
        # to the canvas's documented size right now; a persisted
        # geometry (E8.1.1) replaces that default, so a relaunch opens
        # where the operator left the frame. Either way the size and
        # position then go through the screen fit (Phase 6), so a
        # geometry saved on a larger display -- or on a display that is
        # no longer attached -- comes back fully visible.
        self._apply_frame_geometry(initial_geometry)
        # Phase 6: a monitor change re-fits. wx fires this on the frame
        # when the display arrangement changes, on both platforms.
        self.frame.Bind(wx.EVT_DISPLAY_CHANGED, self._on_display_changed)

        # Phase 6: F2 opens the selected feed row's Crossing Detail.
        # The id is frame-local (wx.NewIdRef) because no menu item owns
        # this command; accelerator_entries() also hands the entry to
        # app._apply_accelerators, so the bootstrap's menubar-derived
        # table cannot drop it after construction.
        self._edit_crossing_id = wx.NewIdRef()
        self.frame.Bind(wx.EVT_MENU, self._on_edit_crossing_accelerator, id=self._edit_crossing_id)
        # Phase 7: Delete/Ctrl+D and Ctrl+E, bound the same frame-local
        # way. Delete and Ctrl+D share one command id -- two keys, one
        # delete flow -- so both rows in accelerator_entries() carry
        # _delete_crossing_id.
        self._delete_crossing_id = wx.NewIdRef()
        self.frame.Bind(
            wx.EVT_MENU, self._on_delete_crossing_accelerator, id=self._delete_crossing_id
        )
        self._edit_plate_crossing_id = wx.NewIdRef()
        self.frame.Bind(
            wx.EVT_MENU, self._on_edit_plate_crossing_accelerator, id=self._edit_plate_crossing_id
        )
        self.frame.SetAcceleratorTable(wx.AcceleratorTable(self.accelerator_entries()))

        # The console-view handle the app (and scenarios) reach the
        # view's own methods through -- the results window's
        # ``frame.presenter`` precedent (results_win.py).
        self.frame.console = self

        self._total_column: Any = None
        self._lap_time_column: Any = None
        self._build_columns()
        self._crossings_model: CrossingsFeedModel | None = None
        # Phase 4: the feed's current header sort, re-applied whenever
        # the model is rebuilt (a new model drops the control's sort
        # key). Unlike the riders tab, the feed always has one: Time
        # ascending, so the oldest crossing is the first row.
        self._feed_sort_column: int = DEFAULT_FEED_SORT[0]
        self._feed_sort_ascending: bool = DEFAULT_FEED_SORT[1]

        # E7.2.1: the menu-enablement binder's seam. set_state fires it
        # on every ride-state change (the epic's "existing ride-state-
        # change seam"), and show_feed fires it too -- the console
        # re-renders the feed on every record/undo/tick, so the binder
        # also refreshes the §15 count conditions (Undo Last Crossing's
        # "≥1 crossing", Audit Trail's "≥1 audit row") within a tick
        # of any engine change, not only on a state transition.
        self._status: RideStatus = RideStatus.DRAFT
        self._on_ride_changed: Callable[[RideStatus], None] | None = None
        # W5: the presenter the render back-calls re-apply the console
        # gates through (set once wire_console/set_presenter runs;
        # constructions that never wire a presenter render no gates).
        self._presenter: ConsolePresenter | None = None
        # D3: the tick timer and plate-submit callback wire_console/
        # wire_entry install. Declared up front so clear_presenter can
        # unbind a console that never wired them without an
        # AttributeError. The R-54 backup timer wire_console builds
        # alongside the tick timer is declared here for the same
        # reason.
        self._tick_timer: wx.Timer | None = None
        self._backup_timer: wx.Timer | None = None
        self._on_submit: Callable[[str], None] | None = None
        # D3: the one-time wiring sentinel. set_presenter binds the
        # entry/lifecycle controls and builds the tick timer on the
        # first attach and never again; clear_presenter detaches a
        # cleared ride's presenter WITHOUT resetting this, so a later
        # attach swaps references and restarts the stopped timer
        # instead of rebinding every control (a duplicate wx.Bind would
        # deliver each later event twice).
        self._wired = False

        # Reflow now that the size and every sizer item are final, so
        # the splitter has its real client area before a sash position
        # is read or restored.
        self.frame.Layout()

        self.main_splitter.Bind(wx.EVT_SPLITTER_SASH_POS_CHANGED, self._on_sash_changed)
        self._restore_sash_position(initial_sash)
        # E8.1.1: persist the frame's placement on move/resize and on
        # close -- the second half of the disk-backed layout store (the
        # app wires on_layout_changed to the settings module). Bound
        # here, ahead of the app's own EVT_CLOSE handler, so a quitting
        # frame saves its final layout before that handler destroys it.
        self.frame.Bind(wx.EVT_MOVE, self._on_geometry_changed)
        self.frame.Bind(wx.EVT_SIZE, self._on_geometry_changed)
        self.frame.Bind(wx.EVT_CLOSE, self._on_frame_close)

        rows = self.data_source.feed_rows()
        self.show_feed(rows)
        self.show_flagged([row for row in rows if row.flagged])
        self.show_riders(self.data_source.riders())
        self.show_counters(self.data_source.counters())

    # ------------------------------------------------------- lookups

    # ``_find`` (DialogFindMixin, ui.views._support) resolves in
    # ``self.frame`` -- the console's window is a wx.Frame, not a
    # dialog.
    _window_attr = "frame"

    # --------------------------------------------------------- gauges

    def _build_clock_dial(self, panel: wx.Panel, name: str) -> RaceClock:
        """Build one ``RaceClock`` into its placeholder panel (WS-D).

        Each clock slot's XRC sizer holds the caption at index 0 and
        the numeric label at index 1; the dial is inserted between
        them so the column reads caption, dial, number.
        """
        dial = RaceClock(panel)
        dial.SetName(name)
        panel.GetSizer().Insert(1, dial, 0, wx.ALIGN_CENTER_HORIZONTAL)
        return dial

    # ------------------------------------------------------- columns

    def _build_columns(self) -> None:
        """Append the feed's eight columns in canvas order.

        Keeps one handle per independently-hidden column: the Total
        column (``_total_column``) and the Lap time column
        (``_lap_time_column``), each toggled by its own show setting
        through :meth:`set_time_columns`.

        Each column gets the explicit width from ``feed_model.
        COLUMN_WIDTHS`` (W9): DataView columns never autosize to
        their content, so unpinned widths truncate or stretch with
        the platform default; the widths live in the wx-free module
        so the values stay headlessly pinned.

        Phase 4: every column also carries :data:`FEED_COLUMN_FLAGS`
        (sortable + resizable), so the platform draws a header arrow
        and orders through :meth:`CrossingsFeedModel.Compare`'s own
        per-column keys.

        Every column is text: the Card column renders the dealt
        card's glyph display (``feed_model.card_text_or_blank``), not
        a bitmap, so all eight share the one renderer.
        """
        for col, label in enumerate(feed_model.COLUMN_LABELS):
            width = feed_model.COLUMN_WIDTHS[col]
            column = self.crossings_list.AppendTextColumn(
                label, col, width=width, flags=FEED_COLUMN_FLAGS
            )
            if col in feed_model.TOTAL_COLUMN:
                self._total_column = column
            if col in feed_model.LAP_TIME_COLUMN:
                self._lap_time_column = column

    def _build_flagged_columns(self) -> tuple[Any, ...]:
        """Append the flagged list's four columns (WS-H).

        One column per :data:`FLAG_COLUMN_LABELS` entry -- Plate | Lap
        | Lap time | Issue -- so the Issue column needs no change here.
        """
        return tuple(
            self.flagged_list.AppendTextColumn(label, col)
            for col, label in enumerate(FLAG_COLUMN_LABELS)
        )

    def _build_riders_columns(self) -> list[Any]:
        """Append ``console_riders_list``'s sortable columns.

        The labels are the shared
        :data:`~rivercrossing.ui.rider_columns.CONSOLE_RIDER_COLUMNS`
        ones (Plate | Name | Team | Sex | Cards) and the widths are
        :data:`RIDERS_COLUMN_WIDTHS`, so the console's list and the
        rider editor's own draw the same headers at the same widths
        (plus Cards). Each column carries
        :data:`RIDERS_LIST_COLUMN_FLAGS`, so the platform draws a
        header arrow and sorts through
        :meth:`~rivercrossing.ui.views._support.RiderRowListModel.
        Compare` -- keyed by the shared column's own ``sort_key``.

        Returns:
            The appended columns in order.
        """
        return [
            self.console_riders_list.AppendTextColumn(
                column.label,
                index,
                width=RIDERS_COLUMN_WIDTHS[index],
                flags=RIDERS_LIST_COLUMN_FLAGS,
            )
            for index, column in enumerate(CONSOLE_RIDER_COLUMNS)
        ]

    def set_on_open_rider(self, callback: Callable[[str], None]) -> None:
        """Register the double-click seam of the riders tab (WS-H).

        The app wires this to its open-entry-detail flow; the console
        itself only fires ``callback(plate)`` when a rider row is
        activated (double-click or Enter with the list focused).
        """
        self._on_open_rider = callback

    def set_on_open_flagged(self, callback: Callable[[str, bool], None]) -> None:
        """Register the activation seam of the flagged tab (W11 F2a).

        The app wires this to its review router; the console itself
        only fires ``callback(plate, held)`` when a flagged row is
        activated (double-click or Enter with the list focused). The
        handler is called with the row's plate and its ``held`` flag:
        a held card (R-34, hold mode) gets a confirm/void decision, a
        credited short lap (always-deal) opens its crossing detail. The
        flagged tab and the riders tab keep separate seams because the
        app opens a different surface for each (review vs the rider
        editor).
        """
        self._on_open_flagged = callback

    def set_on_open_crossing(self, callback: Callable[[int], None]) -> None:
        """Register the crossings feed's activation seam (J2).

        The app wires this to its Crossing Detail flow; the console
        itself only fires ``callback(row)`` when a feed row is
        activated (double-click or Enter with the list focused).
        *row* is the activated row's index into the rendered feed
        model -- the presenter's own search-filtered, newest-first
        row list -- so resolving it back to a live crossing is the
        app's job, not this view's.
        """
        self._on_open_crossing = callback

    def set_on_delete_crossing(self, callback: Callable[[int], None]) -> None:
        """Register the feed's delete seam (Phase 7).

        The app wires this to its delete-confirm flow; the console
        fires ``callback(row)`` when Delete or Ctrl+D is pressed with
        a feed row selected. *row* is the selected row's index into
        the rendered feed model, resolved exactly like
        :meth:`set_on_open_crossing`'s.
        """
        self._on_delete_crossing = callback

    def set_on_edit_plate_crossing(self, callback: Callable[[int], None]) -> None:
        """Register the feed's plate-edit seam (Phase 7).

        The app wires this to its Plate-prompt flow; the console
        fires ``callback(row)`` when Ctrl+E is pressed with a feed row
        selected. *row* is the selected row's index into the rendered
        feed model, resolved exactly like
        :meth:`set_on_open_crossing`'s.
        """
        self._on_edit_plate_crossing = callback

    def accelerator_entries(self) -> list[Any]:
        """Return the frame's own code-side accelerator entries.

        The three menu-backed shortcuts come from ``main.xrc``'s
        ``<accel>`` elements and are harvested by
        ``app._apply_accelerators``; the console's own commands -- F2
        (edit crossing), the feed's Delete/Ctrl+D (delete the selected
        crossing) and Ctrl+E (edit its plate) -- have no menu item to
        harvest, so the console owns their entries here. The app
        appends this list to the harvested ones when it re-applies the
        frame's table at bootstrap -- without that, the frame-level
        bindings made in ``__init__`` would be silently replaced.

        Delete and Ctrl+D are two rows for one command, so both carry
        :attr:`_delete_crossing_id`; only their modifier differs.
        """
        return [
            wx.AcceleratorEntry(wx.ACCEL_NORMAL, EDIT_CROSSING_KEY, self._edit_crossing_id),
            wx.AcceleratorEntry(wx.ACCEL_NORMAL, DELETE_CROSSING_KEY, self._delete_crossing_id),
            wx.AcceleratorEntry(wx.ACCEL_CTRL, DELETE_CROSSING_CTRL_KEY, self._delete_crossing_id),
            wx.AcceleratorEntry(
                wx.ACCEL_CTRL, EDIT_PLATE_CROSSING_KEY, self._edit_plate_crossing_id
            ),
        ]

    def focus_review_panel(self) -> None:
        """Focus the review notebook's "Needs Review" tab (WS-H).

        The Cards ▸ Review Held Cards menu route lands here -- the
        same target as ``review_btn``'s click, so the two never drift:
        show the flagged tab and focus its list. It opens nothing
        itself; the app's open-entry-detail wiring lives behind the
        riders tab's own activation seam.

        Phase 4 makes the Riders tab page 0 (the notebook's default),
        so the page is looked up by its label rather than by a
        hard-coded index -- a future reorder then re-routes this focus
        instead of silently landing the operator on the wrong tab. A
        notebook without that page has nothing to focus, so this
        returns early (the .xrc always declares it).
        """
        page = _page_index(self.review_notebook, NEEDS_REVIEW_PAGE_LABEL)
        if page is None:
            return
        self.review_notebook.SetSelection(page)
        self.flagged_list.SetFocus()

    def _on_column_sorted(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Remember the riders-tab header sort the operator just chose.

        wx's ``EVT_DATAVIEW_COLUMN_SORTED`` fires after the control has
        already reordered its rows through
        :meth:`~rivercrossing.ui.views._support.RiderRowListModel.
        Compare`; this handler keeps the column and direction so
        :meth:`_apply_sort` can restore both after the next
        :meth:`show_riders` rebuild.
        """
        event.Skip()
        column = self.console_riders_list.GetSortingColumn()
        if column is None:
            return
        self._riders_sort_column = column.GetModelColumn()
        self._riders_sort_ascending = column.IsSortOrderAscending()

    def _on_feed_column_sorted(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Remember the crossings header sort the operator just chose.

        The feed's own half of :meth:`_on_column_sorted`: every feed
        column is sortable (Phase 4), and
        :meth:`_apply_feed_sort` re-applies the remembered column
        after the next :meth:`show_feed` rebuild. The default
        (:data:`DEFAULT_FEED_SORT`) stands until a header is clicked.
        """
        event.Skip()
        column = self.crossings_list.GetSortingColumn()
        if column is None:
            return
        self._feed_sort_column = column.GetModelColumn()
        self._feed_sort_ascending = column.IsSortOrderAscending()

    def _apply_feed_sort(self) -> None:
        """Re-apply the feed's current sort to the freshly built model.

        Called from every :meth:`show_feed`: the rebuild associates a
        new model, which drops the sort key the control was holding,
        so the arrow and the row order are restored from
        :attr:`_feed_sort_column`/:attr:`_feed_sort_ascending` (Time
        ascending until an operator clicks a header). The
        ``UnsetAsSortKey`` first is the load-bearing macOS step
        :meth:`_apply_sort` documents: ``SetSortOrder`` is a no-op
        when the direction is unchanged, so without the clear the
        rebuilt model would keep the presenter's own order and the
        sort would silently revert.

        The clear is guarded by ``IsSortKey``: on the first render the
        column has never sorted the control, and Windows' generic
        ``DataViewColumn.UnsetAsSortKey`` asserts ("column is not used
        for sorting") and aborts the process there.

        Unlike the riders tab's own sort, this one always applies: the
        feed opens sorted by Time ASCENDING (the ride's own reading
        order), so there is no "nothing sorted yet" state.
        """
        model = self._crossings_model
        if model is None:
            return
        column = self.crossings_list.GetColumn(self._feed_sort_column)
        if column is None:
            return
        if column.IsSortKey():
            column.UnsetAsSortKey()
        column.SetSortOrder(self._feed_sort_ascending)
        model.Resort()

    def _apply_sort(self) -> None:
        """Re-apply the remembered riders-tab sort to the current model.

        :meth:`show_riders` rebuilds the model on every tick, which
        drops the sort key the control was holding; setting it on the
        column again and asking the model to resort restores exactly
        the order the operator left the tab in. No column is
        remembered until a header is clicked, so the first render
        keeps the source's own order.

        The ``UnsetAsSortKey`` first is load-bearing on macOS
        (measured): ``SetSortOrder`` is a no-op when the direction is
        unchanged, so without the clear the rebuilt model keeps the
        source's order and the operator's sort silently reverts on
        the next tick.
        """
        model = self._riders_model
        if model is None or self._riders_sort_column is None:
            return
        column = self.console_riders_list.GetColumn(self._riders_sort_column)
        if column is None:
            return
        column.UnsetAsSortKey()
        column.SetSortOrder(self._riders_sort_ascending)
        model.Resort()

    def _fire_open_flagged(self, row: int) -> None:
        """Fire the flagged-open seam for model row *row* (W11 F2a/§6).

        The one place the tab's ``(plate, held)`` pair is assembled,
        shared by the row's own activation and by ``review_btn`` so the
        two routes cannot drift. An unwired console (no app yet, test
        constructions) leaves the row untouched.
        """
        if self._flagged_model is None or self._on_open_flagged is None:
            return
        plate = self._flagged_model.GetValueByRow(row, FLAG_COL_PLATE)
        self._on_open_flagged(plate, self._flagged_model.held_for_row(row))

    def _on_review_clicked(self) -> None:
        """Handle ``review_btn``: the sidebar's "Review…" affordance.

        Shows and focuses the Needs Review tab (WS-H) and then acts on
        the row the operator has selected -- the same routing a
        double-click fires (plan §6). With nothing selected the button
        keeps its original focus-only behavior, which is also the
        ``Cards ▸ Review Held Cards`` menu route's
        :meth:`focus_review_panel` target.

        ``flagged_list`` is a plain ``DataViewCtrl``, so its selection
        arrives as a ``DataViewItem`` that the model resolves to a row;
        the ``GetSelectedRow`` of ``wxDataViewListCtrl`` does not exist
        here. Resolve it exactly as :meth:`_on_flagged_activated` does.
        """
        self.focus_review_panel()
        if self._flagged_model is None:
            return
        item = self.flagged_list.GetSelection()
        if not item.IsOk():
            return
        row = self._flagged_model.GetRow(item)
        if row == wx.NOT_FOUND:
            return
        self._fire_open_flagged(row)

    def _on_rider_activated(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Fire the open-rider seam with the activated row's plate."""
        if self._riders_model is None:
            return
        row = self._riders_model.GetRow(event.GetItem())
        if row == wx.NOT_FOUND:
            return
        plate = self._riders_model.GetValueByRow(row, RIDERS_COL_PLATE)
        if self._on_open_rider is not None:
            self._on_open_rider(plate)

    def _on_flagged_activated(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Fire the open-flagged seam with the row's plate + held flag.

        W11 F2a: the mirror of :meth:`_on_rider_activated` for the
        flagged list. The row's plate and its card disposition go to
        the app's review router (:meth:`_fire_open_flagged`), which
        decides between the confirm/void decision and the crossing
        detail.
        """
        if self._flagged_model is None:
            return
        row = self._flagged_model.GetRow(event.GetItem())
        if row == wx.NOT_FOUND:
            return
        self._fire_open_flagged(row)

    def _on_crossing_activated(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Fire the open-crossing seam with the activated feed row (J2).

        The feed's own model answers the row index, exactly as the
        two review lists' handlers resolve theirs; the app turns that
        index back into the live ``Crossing`` (the view holds no
        crossings, only the rows the presenter rendered) -- resolved
        against the rows actually rendered, search filter included
        (``ConsolePresenter.rendered_feed_rows``).
        """
        if self._crossings_model is None:
            return
        row = self._crossings_model.GetRow(event.GetItem())
        if row == wx.NOT_FOUND:
            return
        if self._on_open_crossing is not None:
            self._on_open_crossing(row)

    def _on_edit_crossing_accelerator(self, _event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Open the selected feed row's Crossing Detail on F2 (Phase 6).

        The keyboard twin of :meth:`_on_crossing_activated`: that
        handler takes the activated item from its event, this one takes
        the feed's *selection* (F2 is not tied to a click) and resolves
        it through the same model. Both fire the same
        :meth:`set_on_open_crossing` seam with a row index, so the app's
        row-to-``Crossing`` resolution is shared. Nothing selected --
        or a stale selection the model cannot resolve -- is a no-op.
        """
        if self._crossings_model is None:
            return
        item = self.crossings_list.GetSelection()
        if not item.IsOk():
            return
        row = self._crossings_model.GetRow(item)
        if row == wx.NOT_FOUND:
            return
        if self._on_open_crossing is not None:
            self._on_open_crossing(row)

    def _on_delete_crossing_accelerator(
        self,
        _event: Any,  # noqa: ANN401 -- wx ships no stubs
    ) -> None:
        """Delete the selected feed row's crossing on Delete or Ctrl+D.

        Phase 7: :meth:`_on_edit_crossing_accelerator`'s own shape with
        the delete seam -- the same model resolution, so a stale or
        absent selection is a no-op and a row index is all the app
        gets. The app runs the Crossing Detail danger confirm on it
        (``set_on_delete_crossing``).
        """
        if self._crossings_model is None:
            return
        item = self.crossings_list.GetSelection()
        if not item.IsOk():
            return
        row = self._crossings_model.GetRow(item)
        if row == wx.NOT_FOUND:
            return
        if self._on_delete_crossing is not None:
            self._on_delete_crossing(row)

    def _on_edit_plate_crossing_accelerator(
        self,
        _event: Any,  # noqa: ANN401 -- wx ships no stubs
    ) -> None:
        """Edit the selected feed row's plate on Ctrl+E (Phase 7).

        The delete handler's third twin: the selection resolves through
        the feed's model and the row index goes to the
        :meth:`set_on_edit_plate_crossing` seam, which the app runs
        through ``run_plate_dialog`` and ``reassign_crossing_plate``.
        """
        if self._crossings_model is None:
            return
        item = self.crossings_list.GetSelection()
        if not item.IsOk():
            return
        row = self._crossings_model.GetRow(item)
        if row == wx.NOT_FOUND:
            return
        if self._on_edit_plate_crossing is not None:
            self._on_edit_plate_crossing(row)

    def _on_search_text(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Forward the ``crossings_search`` box's current text.

        ``crossings_search`` is a ``wxSearchCtrl``: text changes
        (typing, a programmatic ``SetValue``, the native clear X) all
        re-run the filter, and the search button (Enter) does too --
        every path reads the control's current value, so one handler
        serves all three events (the rider editor's own precedent).

        The presenter owns the filter, so this only forwards. A
        console with no presenter (the no-ride bootstrap) has no feed
        to narrow, and the box simply does nothing.
        """
        event.Skip()
        if self._presenter is not None:
            self._presenter.on_search_text(self.crossings_search.GetValue())

    def set_time_columns(self, *, show_total: bool, show_lap: bool) -> None:
        """Show or hide the Total/Lap time columns independently (R-37).

        Each flag drives its own column: the Total column and the Lap
        time column hide and show separately. The clock
        (``clock_elapsed_lbl``/``clock_remaining_lbl``) is untouched --
        R-37 keeps it visible regardless of these settings.
        """
        self._total_column.SetHidden(not show_total)
        self._lap_time_column.SetHidden(not show_lap)

    # --------------------------------------------- frame size, splitter

    def _apply_frame_geometry(self, initial_geometry: tuple[int, int, int, int] | None) -> None:
        """Size and place the frame, then fit it to its display.

        ``initial_geometry`` is E8.1.1's persisted placement and always
        wins when present; ``None`` (no saved layout yet) opens at the
        canvas's own :data:`MIN_SIZE`. Either way
        :func:`~rivercrossing.ui.views._support.fit_frame_to_screen`
        then clamps the result to the current display's work area --
        so a geometry saved on a larger display, or on one that is no
        longer attached, opens whole and on screen, and the frame's
        minimum size is never taller or wider than the screen itself
        (CODINGSTANDARDS-UX-DESKTOP.md section 6).
        """
        if initial_geometry is not None:
            x, y, width, height = initial_geometry
            self.frame.SetPosition((x, y))
            self.frame.SetSize((width, height))
        else:
            self.frame.SetSize(wx.Size(*MIN_SIZE))
        fit_frame_to_screen(self.frame, MIN_SIZE)

    def _on_display_changed(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Re-fit the frame when the display arrangement changes.

        Dragging the console to a smaller monitor, unplugging the one
        it was on, or changing the resolution re-runs the same fit the
        constructor ran, so the window can never be left larger than --
        or entirely off -- the display it is now on.
        """
        event.Skip()
        fit_frame_to_screen(self.frame, MIN_SIZE)

    def _restore_sash_position(self, initial_sash: int | None) -> None:
        """Apply the persisted sash position, or the W9 default.

        ``initial_sash`` is E8.1.1's persisted position and always
        wins when present; ``None`` (no saved layout yet) falls back
        to :data:`DEFAULT_SASH`, so a fresh launch opens with the
        feed pane at the pinned width instead of wherever the XRC
        shell's bare splitter happens to land.
        """
        sash = initial_sash if initial_sash is not None else DEFAULT_SASH
        self.main_splitter.SetSashPosition(sash)

    def persist_layout(self) -> None:
        """Report the current sash position and frame geometry (E8.1.1).

        Fires the constructor's ``on_layout_changed`` callback, which
        the app bootstrap wires to the settings store; an unwired
        frame (test constructions) reports nothing. wx only fires the
        sash event from genuine user drag input, never from a
        programmatic ``SetSashPosition`` (measured), so tests call
        this directly instead (CODINGSTANDARDS-UX-DESKTOP.md section
        6: sash positions must persist across restarts). The
        move/size/close handlers all route through this one seam.
        """
        if self._on_layout_changed is None:
            return
        position = self.frame.GetPosition()
        size = self.frame.GetSize()
        geometry = (position.x, position.y, size.width, size.height)
        self._on_layout_changed(self.main_splitter.GetSashPosition(), geometry)

    def _on_sash_changed(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Persist a real, user-driven sash drag."""
        event.Skip()
        self.persist_layout()

    def _on_geometry_changed(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Persist a move or resize, then let the event continue.

        No debounce: the settings file is a few hundred bytes and the
        write is an atomic replace, so a drag's dozens of events cost
        less than a layout-save timer's destroy hazard would (the
        ``_tick_timer`` segfault precedent).
        """
        event.Skip()
        self.persist_layout()

    def _on_frame_close(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Persist the layout, then let the app's close flow run.

        ``event.Skip()`` is what lets the app bootstrap's own
        ``EVT_CLOSE`` handler (bound after this one) still run.
        """
        self.persist_layout()
        event.Skip()

    # ----------------------------------------------------- ConsoleView

    def show_feed(self, rows: list[FeedRow]) -> None:
        """Render the crossings feed, newest first (ConsoleView).

        Also fires the E7.2.1 menu-binder seam: the feed re-renders on
        every record/undo/tick, so §15 count conditions refresh within
        a tick of any engine change (see the constructor comment), and
        re-applies the W5 console-button gates through the bound
        presenter (record/undo/tick are exactly the crossing-count
        changes the Start/Undo verdicts depend on).

        Phase 4: the renderer is told the row order the *presenter*
        chose -- the feed's newest-first order, narrowed by the search
        box; the list's own header sort is then re-applied
        (:meth:`_apply_feed_sort`), which is what actually paints the
        operator's chosen order onto the new model.
        """
        self._crossings_model = CrossingsFeedModel(rows)
        self.crossings_list.AssociateModel(self._crossings_model)
        self._apply_feed_sort()
        self._notify_ride_changed()
        if self._presenter is not None:
            self._presenter.refresh_console_gates()

    def show_flagged(self, rows: list[FeedRow]) -> None:
        """Render the review notebook's flagged rows (ConsoleView).

        WS-H: the presenter feeds the flagged subset of the feed here
        (its ``refresh_feed``) -- every short lap, held or credited --
        and the view rebuilds the model like :meth:`show_feed` does:
        fresh rows each call keeps the row-count bookkeeping trivial.
        The phase-4 search box narrows the crossings list only, so this
        tab keeps every flagged row whatever the box holds.
        """
        self._flagged_model = FlaggedListModel(rows)
        self.flagged_list.AssociateModel(self._flagged_model)

    def show_riders(self, rows: list[RiderRow]) -> None:
        """Render the review notebook's riders rows (ConsoleView).

        WS-H: fed by the presenter's ``_refresh_riders`` on the tick
        (``DataSource.riders()``), and once at construction. The model
        renders the shared
        :data:`~rivercrossing.ui.rider_columns.CONSOLE_RIDER_COLUMNS`
        -- Plate | Name | Team | Sex | Cards -- so this list's cells are
        the rider editor's own plus the live credited card codes, and
        the rows keep the source's own order: the tab's native header
        sort (answered by ``RiderRowListModel.Compare``) is re-applied
        to this new model by :meth:`_apply_sort`.
        """
        self._riders_model = RiderRowListModel(rows, CONSOLE_RIDER_COLUMNS)
        self.console_riders_list.AssociateModel(self._riders_model)
        self._apply_sort()

    def show_counters(self, c: Counters) -> None:
        """Render the six counter chips (ConsoleView)."""
        self.crossings_count_lbl.SetLabel(_format_count(c.crossings))
        self.cards_count_lbl.SetLabel(_format_count(c.cards_dealt))
        self.on_course_lbl.SetLabel(_format_count(c.on_course))
        self.shoe_lbl.SetLabel(f"{c.shoe_remaining}/{c.shoe_total}")
        self.riders_count_lbl.SetLabel(_format_count(c.riders))
        self.teams_count_lbl.SetLabel(_format_count(c.teams))

    def show_current_lap(self, lap: int) -> None:
        """Render the header's two-digit Current Lap reading (Phase 6).

        ``ConsoleView.show_current_lap``: *lap* is the highest lap
        number any entry has recorded (``console.current_lap``), so the
        first rider crossing moves the reading 00 -> 01. The value is
        always two digits and capped at :data:`MAX_CURRENT_LAP` -- the
        label is width-pinned to that two-digit extent
        (``__init__``), so a third character could not be shown without
        reflowing the box beside it.
        """
        self.current_lap_lbl.SetLabel(f"{min(max(lap, 0), MAX_CURRENT_LAP):02d}")

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Show or hide the Teams chip with its caption (R-11, W12).

        A solo-only ride hides the whole team UI, the sidebar's Teams
        chip included. The caption carries no frozen name (every chip
        caption is unnamed), so the view finds it structurally: it is
        the item immediately before the value inside the chip's own
        vertical sizer. That is ``rider_editor._set_team_choice_row_
        visible``'s precedent. The frame re-layouts so the grid row
        collapses and the notebook keeps its share of the sidebar.
        """
        sizer = self.teams_count_lbl.GetContainingSizer()
        items = list(sizer.GetChildren())
        index = next(i for i, item in enumerate(items) if item.GetWindow() is self.teams_count_lbl)
        caption = items[index - 1].GetWindow()
        self.teams_count_lbl.Show(visible)
        caption.Show(visible)
        self.frame.Layout()

    def flash_crossing(self, r: FeedRow) -> None:
        """Highlight the just-recorded crossing (ConsoleView)."""
        self.last_crossing_lbl.SetLabel(feed_model.flash_crossing_label(r))

    def set_state(self, status: RideStatus, *, stopped: bool = False) -> None:
        """Reflect the ride's lifecycle state (ConsoleView).

        The status label and record-crossing row enablement (A4:
        ``record_btn`` tracks ``plate_input``, both live only in
        RUNNING), plus the REOPENED state's corrections banner:
        REOPENED is a corrections-only state (spec §3, R-36), so the
        console posts :data:`REOPENED_BANNER` to the status bar's first
        field to say entry is off and corrections are on (E5.2.2). The
        banner is status-bar text, not a top-of-window InfoBar, so its
        text can never change the frame's own height (G4); every other
        status leaves that field untouched -- the operator's last notice
        stands. FINISHED carries no banner -- its red lamp and
        "FINISHED" label hold the state, and the status bar's own
        notice names the Results menu.

        This is the E7.2.1 menu-binder's "ride-state-change seam":
        every presenter state transition (start/stop/finish/reopen)
        lands here, and the binder re-applies the §15 enablement.
        WS-D adds the status lamp: the same state transition drives
        the ``StopLight`` through ``console.stop_light_mode``. W6:
        a stopped RUNNING ride's *stopped* flag renders the STOPPED
        label and the amber lamp through ``console.status_text``. C2
        refreshes the Start/Stop/Undo button gates through the bound
        presenter (the single source for all three).
        """
        self.ride_status_lbl.SetLabel(status_text(status, stopped=stopped))
        self.ride_status_light.set_mode(stop_light_mode(status, stopped=stopped))
        running = status == RideStatus.RUNNING
        self.plate_input.Enable(running)
        self.record_btn.Enable(running)
        if status is RideStatus.REOPENED:
            self.show_notice(REOPENED_BANNER)
        self._status = status
        self._notify_ride_changed()
        if self._presenter is not None:
            self._presenter.refresh_console_gates()

    def show_no_ride(self) -> None:
        """Render the true no-ride empty state (W1, R-55/R-80).

        The app bootstrap calls this directly when no store-backed
        ride is open, so it is deliberately not part of the
        ``ConsoleView`` Protocol -- with no ride there is no presenter
        to call it. The ride-info group is blank (every value row
        cleared, the logo slot hidden), the status column reads
        :data:`NO_RIDE_STATUS_TEXT` (CREATE RIDE -- the operator has to
        create one; a DRAFT reading would suggest one already exists)
        with its lamp lit red (Phase 6 -- a labelled state is never
        carried by colour alone, UX-DESKTOP section 7), every ride
        control is inert, and the clocks/feed/counters/lap show their
        zero state. ``_status``
        returns to DRAFT so the menu binder's ride-state seam sees
        DRAFT; the app's own ``ride_open=False`` state keeps the
        ride-gated rows off.
        """
        for value in (
            self.ride_name_value,
            self.ride_date_value,
            self.ride_venue_value,
            self.ride_organizer_value,
            self.ride_scorer_value,
            self.ride_lap_km_value,
        ):
            value.SetValue("")
        self.ride_logo_bmp.Hide()
        self.ride_status_lbl.SetLabel(NO_RIDE_STATUS_TEXT)
        self.ride_status_light.set_mode(stop_light_mode(RideStatus.DRAFT))
        # W1: every ride control is inert with no ride to act on.
        for control in (
            self.plate_input,
            self.record_btn,
            self.start_btn,
            self.stop_btn,
            self.undo_btn,
        ):
            control.Enable(False)  # noqa: FBT003 -- wx API takes a positional bool
        self.show_clock("0:00:00", "0:00:00")
        self.set_clock_fractions(elapsed_frac=0.0, remaining_frac=0.0)
        self.show_feed([])
        self.show_flagged([])
        self.show_riders([])
        self.show_counters(Counters(0, 0, 0, 0, 0, 0, 0))
        self.show_current_lap(0)
        self._status = RideStatus.DRAFT
        self._notify_ride_changed()

    def show_ride_header(  # noqa: PLR0913 -- the ride-info group's facts
        self,
        *,
        name: str,
        logo: Path | None,
        event_date: date,
        planned_start: datetime,  # noqa: ARG002 -- kept for the app's one call shape
        entry_mode: EntryMode,  # noqa: ARG002 -- kept for the app's one call shape
        venue: str,
        organizer: str,
        scorer: str,
        lap_km: float,
    ) -> None:
        """Render the open ride's ride-info group (ConsoleView, C1/§5).

        One seam for every console switch onto a ride -- the library
        Open, the New Ride flow and Edit Ride alike: the Ride box's six
        read-only values (``ride_name_value`` and its five siblings) and
        the ride's own logo in ``ride_logo_bmp``, scaled into
        :data:`RIDE_LOGO_DISPLAY_SIZE`.

        The date renders ISO (``event_date``'s own storage shape) and
        the lap length renders as ``str(lap_km)``; the values are never
        typed into, so no other formatting is invented for them.

        ``planned_start``/``entry_mode`` are deliberately not rendered
        here -- the group shows name/date/venue/organizer/scorer/lap km
        only -- but stay in the signature so the app's single header
        seam and its recording fakes keep one call shape.
        """
        self.ride_name_value.SetValue(name)
        self.ride_date_value.SetValue(event_date.isoformat())
        self.ride_venue_value.SetValue(venue)
        self.ride_organizer_value.SetValue(organizer)
        self.ride_scorer_value.SetValue(scorer)
        self.ride_lap_km_value.SetValue(str(lap_km))
        self._show_ride_logo(logo)
        self.frame.Layout()

    def _show_ride_logo(self, logo: Path | None) -> None:
        """Render the ride logo into its slot, or hide the slot (C1).

        A logo that is absent, missing on disk or undecodable hides the
        slot (the same "never blank the canvas" rule ``views.about``
        follows) -- the six value rows above carry the identity on their
        own now, so there is no fallback line to fall back to.
        """
        bitmap = _ride_logo_bitmap(logo)
        if bitmap is None:
            self.ride_logo_bmp.Hide()
            return
        self.ride_logo_bmp.SetBitmap(bitmap)
        self.ride_logo_bmp.Show()

    def set_on_ride_changed(self, callback: Callable[[RideStatus], None]) -> None:
        """Register the menu-binder callback fired on ride changes.

        The app bootstrap wires this to its ``_apply_menu_state``;
        fired from :meth:`set_state` (state transitions) and
        :meth:`show_feed` (count-condition refreshes). Not part of the
        ``ConsoleView`` Protocol -- it is an app-level seam, set once
        after construction.
        """
        self._on_ride_changed = callback

    def _notify_ride_changed(self) -> None:
        """Fire the binder seam, if one is registered."""
        if self._on_ride_changed is not None:
            self._on_ride_changed(self._status)

    def focus_entry(self) -> None:
        """Return focus to the plate entry field (ConsoleView)."""
        self.plate_input.SetFocus()

    def show_notice(self, text: str) -> None:
        """Post *text* to the status bar's first field (ConsoleView)."""
        self.frame.SetStatusText(text, 0)

    def clear_entry(self) -> None:
        """Empty the plate entry field (ConsoleView).

        ``ChangeValue``, not ``SetValue``: wx's own documented
        contract is that ``ChangeValue`` does not fire ``EVT_TEXT``,
        so clearing the field after a submit cannot loop back into
        any future EVT_TEXT-driven validation.
        """
        self.plate_input.ChangeValue("")

    def wire_entry(self, on_submit: Callable[[str], None]) -> None:
        """Bind Enter and Record to *on_submit* with the field's text.

        Binds ``EVT_TEXT_ENTER`` (``plate_input`` carries
        ``wxTE_PROCESS_ENTER``) and ``EVT_BUTTON`` (``record_btn``)
        to the same call. Neither handler calls ``event.Skip()``,
        and ``record_btn`` never gets ``SetDefault()``: with
        ``wxTE_PROCESS_ENTER``, ``Skip()`` would fall through to
        wx's own default-button dispatch and fire a second submit
        for the one Enter keypress.

        The callback is stored as :attr:`_on_submit` and every
        handler routes through it, so :meth:`set_presenter` can swap
        the console onto a new ride without rebinding (E5.4.1's
        library Open); :meth:`clear_presenter` returns it to ``None``
        for the no-ride state.
        """
        self._on_submit = on_submit

        def _submit(_event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
            callback = self._on_submit
            if callback is not None:
                callback(self.plate_input.GetValue())

        self.plate_input.Bind(wx.EVT_TEXT_ENTER, _submit)
        self.record_btn.Bind(wx.EVT_BUTTON, _submit)

    def play(self, cue: Cue) -> None:
        """Play the audio cue for the given event (ConsoleView, R-31).

        Delegates to ``ui.sound``'s default player (E4.4.3); the
        player is wx-lazy and never blocks the entry field (spec §10).
        """
        sound.play(cue)

    def set_stop_enabled(self, *, enabled: bool) -> None:
        """Enable or disable the Stop button (ConsoleView, C2).

        The presenter's ``refresh_console_gates`` owns the verdict
        (RUNNING and not stopped); the view only applies it.
        """
        self.stop_btn.Enable(enabled)

    def set_start_enabled(self, *, enabled: bool) -> None:
        """Enable or disable the Start ride button (ConsoleView, W5)."""
        self.start_btn.Enable(enabled)

    def set_undo_enabled(self, *, enabled: bool) -> None:
        """Enable or disable the Undo last button (ConsoleView, W5)."""
        self.undo_btn.Enable(enabled)

    def show_warning(self, title: str, message: str) -> None:
        """Show *message* as a modal warning over this console (W5).

        The presenter's empty-roster-stop alert; the view owns the
        parent and opens the native dialog, so the presenter stays
        headless and wx-free.
        """
        std_dialogs.show_warning(self.frame, title, message)

    def show_start_blocked(self, reasons: list[str]) -> None:
        """List *reasons* in the blocked-start dialog (ConsoleView).

        ``ConsolePresenter.on_start`` (Phase 5) routes a
        :class:`~rivercrossing.ride.StartBlockedError` here: one row
        per blocking issue, a single OK to dismiss, and the ride left
        un-started either way. The view owns the parent window and the
        modal show -- ``run_dialog`` restores focus to this frame -- so
        the presenter stays headless and wx-free. A resource that
        cannot load the dialog is a no-op: there is nothing else this
        route could do, and the operator can still fix the ride.

        The model is held on the frame for the modal's lifetime, the
        same keep-alive ``show_feed``/``show_flagged`` give theirs.

        After loading, the dialog is fitted and then opened at
        :func:`_start_blocked_size`'s doubled width and height: XRC
        gives a window no min-size, so the one "Issue" column would
        otherwise clip its text (``CsvPreviewDialog._apply_min_size``'s
        idiom).
        """
        window = load_dialog(wx.xrc.XmlResource.Get(), ids.START_BLOCKED_DLG)
        # logic-coverage-exempt: T-3 -- a None window means the XRC
        # handler could not build the dialog at all; unreachable from a
        # headless unit test (ui/views is coverage-omitted).
        if window is None:
            return
        try:
            issue_list = find_control(window, ids.START_BLOCKED_LIST, wx.dataview.DataViewCtrl)
            issue_list.AppendTextColumn(START_BLOCKED_COLUMN_LABELS[0], 0)
            self._start_blocked_model = StartBlockedListModel(reasons)
            associate_model(issue_list, self._start_blocked_model)
            window.Fit()
            size = _start_blocked_size((window.GetSize().width, window.GetSize().height))
            window.SetMinSize(wx.Size(*size))
            window.SetSize(wx.Size(*size))
            dialogs.run_dialog(window, opener=self.frame)
        finally:
            # Fault A's close guard (rider_issues.py's own finally):
            # a post-load raise must not leave the dialog alive.
            if not window.IsBeingDeleted():
                window.Destroy()

    def confirm(  # noqa: PLR0913 -- (title, message) + 2 button labels + danger
        self,
        title: str,
        message: str,
        *,
        ok_label: str,
        cancel_label: str,
        danger: bool = False,
    ) -> bool:
        """Ask a destructive confirm; return whether OK was chosen (W5).

        The native stop confirm behind ``ConsolePresenter.
        on_stop_requested``: the view owns the parent window and the
        modal call, returning only the boolean verdict to the
        presenter. *danger* selects the error-icon dialog
        (``std_dialogs.show_danger``) for a data-losing question --
        Undo Last Crossing (I); the ordinary caution confirm is the
        default.
        """
        show = std_dialogs.show_danger if danger else std_dialogs.show_confirm
        return show(self.frame, title, message, ok_label, cancel_label) == int(wx.ID_OK)

    def show_clock(self, elapsed: str, remaining: str) -> None:
        """Render the ride clock's numeric labels (ConsoleView, R-30).

        The labels sit under the WS-D dials; ``clock_remaining_lbl``
        carries no canvas label, and the elapsed label's default
        ``0:00:00`` comes from main.xrc.
        """
        self.clock_elapsed_lbl.SetLabel(elapsed)
        self.clock_remaining_lbl.SetLabel(remaining)
        self.frame.Layout()

    def set_clock_fractions(self, *, elapsed_frac: float, remaining_frac: float) -> None:
        """Drive the two gauge-clock dials (ConsoleView, WS-D).

        The elapsed dial fills toward 1.0 as the ride runs, the
        remaining dial drains toward 0.0; both hand positions come
        pre-clamped from the presenter's planned-duration math.
        """
        self.elapsed_clock.set_fraction(elapsed_frac)
        self.remaining_clock.set_fraction(remaining_frac)

    def set_entry_locked(self, *, locked: bool) -> None:
        """Lock or unlock the plate entry row (ConsoleView, R-35).

        Stop is a guard, not a state: after ``engine.stop()`` the ride
        still reads RUNNING, so ``set_state`` would re-enable the
        entry row -- this is the "only confirming locks the entry
        field" channel on top of it.
        """
        self.plate_input.Enable(not locked)
        self.record_btn.Enable(not locked)

    def wire_console(self, presenter: ConsolePresenter) -> None:
        """Bind the lifecycle controls and tick timer to *presenter*.

        Mirrors :meth:`wire_entry`'s callback idiom: Start Ride, Stop
        Ride and Undo each forward to the presenter, and a 1 s
        ``wx.Timer`` drives ``presenter.tick()`` (feed/counters/clock
        refresh -- the sole live-clock driver, so C3's closed-ride
        freeze lives in the presenter). W5: Stop Ride forwards
        straight to the presenter's native stop-confirm flow
        (``on_stop_requested``) -- the view opens no dialog itself;
        the retired ``stop_confirm_dlg`` load lived here before.

        R-54's hourly automatic backup gets its own frame-owned timer
        here too, ticking the store-backed scheduler the app threaded
        in as ``backup_scheduler`` (``None`` wires none). It is
        independent of the presenter: clearing a ride stops the tick
        timer, never the hourly backup.

        The presenter is stored as :attr:`_presenter` and every
        handler routes through it, so :meth:`set_presenter` can swap
        the console onto a store-loaded ride without rebinding the
        controls or the timer (E5.4.1's library Open).
        """
        self._presenter = presenter
        self.start_btn.Bind(wx.EVT_BUTTON, lambda _event: self._presenter.on_start())
        self.stop_btn.Bind(wx.EVT_BUTTON, lambda _event: self._presenter.on_stop_requested())
        self.undo_btn.Bind(wx.EVT_BUTTON, lambda _event: self._presenter.on_undo())
        self._tick_timer = wx.Timer(self.frame)
        self.frame.Bind(wx.EVT_TIMER, lambda _event: self._presenter.tick(), self._tick_timer)
        self._tick_timer.Start(_TICK_MS)
        # R-54: the hourly automatic backup, on the tick timer's own
        # shape -- an owner-scoped wx.Timer whose event drives one
        # call. The scheduler (not this interval) decides whether a
        # whole hour has passed since the last backup, so the interval
        # only has to be no longer than an hour. It is independent of
        # the presenter: a cleared ride stops the tick timer, never the
        # hourly backup. No scheduler (a no-store bootstrap) builds no
        # second timer.
        scheduler = self._backup_scheduler
        if scheduler is not None:
            self._backup_timer = wx.Timer(self.frame)
            self.frame.Bind(wx.EVT_TIMER, lambda _event: scheduler.tick(), self._backup_timer)
            self._backup_timer.Start(_BACKUP_MS)
        # Stop the timers with the frame: a running wx.Timer whose owner
        # was destroyed keeps its native timer registered, and the next
        # wxSafeYield dispatches wxTimerImpl::SendEvent against the
        # freed owner -- a measured segfault (reproduced
        # deterministically: build frame -> destroy -> SafeYield past
        # the tick period). ONE handler stops both, because a second
        # wx.EVT_WINDOW_DESTROY binding with no source REPLACES the
        # first (measured on wxPython 4.3.1): a separate backup-timer
        # binding left the tick timer running, and the functional
        # open/quit smoke segfaulted on exactly that dispatch.
        self.frame.Bind(wx.EVT_WINDOW_DESTROY, self._on_frame_destroy)

    def _on_frame_destroy(self, _event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Stop this console's timers as its frame is destroyed (R-54).

        The measured segfault remedy :meth:`wire_console` documents: a
        running ``wx.Timer`` outlives its destroyed owner's native
        registration otherwise, and the next ``wxSafeYield``
        dispatches into freed memory. Both timers go through this one
        handler, since a second ``EVT_WINDOW_DESTROY`` binding on the
        same window replaces the first.
        """
        if self._tick_timer is not None:
            self._tick_timer.Stop()
        if self._backup_timer is not None:
            self._backup_timer.Stop()

    def set_presenter(self, presenter: ConsolePresenter) -> None:
        """Swap the console's bound presenter (E5.4.1 library Open).

        :meth:`wire_entry`/:meth:`wire_console` route every handler
        through :attr:`_on_submit`/:attr:`_presenter`, so replacing
        those two references rewires the whole console -- plate entry,
        start/arm/stop/undo, the tick timer -- without rebinding any
        control or starting a second timer. The caller then re-renders
        state/feed/counters from the new presenter's source.

        W1: the no-ride bootstrap wires no presenter at all, so the
        first ride attach is what performs the one-time
        :meth:`wire_entry`/:meth:`wire_console` binding (plate entry,
        lifecycle controls, tick timer). :attr:`_wired` is the
        "never wired" sentinel, so those binds run exactly once per
        frame; :meth:`clear_presenter` detaches a cleared ride's
        presenter without resetting it, so a later attach swaps the
        references and restarts the stopped timer instead of rebinding.
        """
        if not self._wired:
            self.wire_entry(presenter.on_plate_entered)
            self.wire_console(presenter)
            self._wired = True
            return
        self._on_submit = presenter.on_plate_entered
        self._presenter = presenter
        if self._tick_timer is not None and not self._tick_timer.IsRunning():
            self._tick_timer.Start(_TICK_MS)

    def clear_presenter(self) -> None:
        """Detach the console's presenter for a cleared ride (D3).

        Ride ▸ Clear Ride… removes the ride from the screen only, so
        the console must stop answering its old presenter: the tick
        timer is stopped (its callback would otherwise call ``tick()``
        on the cleared reference) and the plate-submit callback is
        unbound. :attr:`_wired` deliberately stays set, so the next
        attach through :meth:`set_presenter` swaps the references and
        restarts the stopped timer -- the controls bind exactly once
        per frame, however many clear/open cycles the operator runs.

        The timer itself is kept, not nulled: the frame's own
        ``EVT_WINDOW_DESTROY`` handler still stops it at teardown.
        """
        if self._tick_timer is not None:
            self._tick_timer.Stop()
        self._presenter = None
        self._on_submit = None


def _page_index(notebook: wx.Notebook, label: str) -> int | None:
    """Return the index of the page labelled *label*, or ``None``.

    Pages are addressed by their label (the ``<label>`` text
    ``main.xrc`` declares) rather than by a hard-coded index, so
    reordering the review notebook's pages cannot silently point a
    handler at the wrong tab. A notebook with no such page reports
    ``None`` for the caller to handle.
    """
    for index in range(notebook.GetPageCount()):
        if notebook.GetPageText(index) == label:
            return index
    return None


def _ride_logo_bitmap(logo: Path | None) -> Any | None:  # noqa: ANN401 -- wx ships no stubs
    """Return *logo*'s fitted PNG bitmap, or ``None``.

    ``None`` (no logo chosen) is the header's hidden-slot case; a path
    that names no file, or bytes wx cannot decode, decodes to a not-OK
    ``wx.Bitmap`` and is treated the same way -- measured on wxPython
    4.3.1: ``wx.Bitmap(path, wx.BITMAP_TYPE_PNG)`` returns a not-OK
    bitmap for both, rather than raising.

    A decodable logo is scaled to fit :data:`RIDE_LOGO_DISPLAY_SIZE`
    (aspect preserved, never upscaled) so a large logo cannot blow the
    header row open and a small one keeps its own pixels -- the fitted
    dimensions come from ``team_editor.logo_fit_size``, the one fit
    rule the setup dialog's own previews already use.

    Returns:
        A valid ``wx.Bitmap`` inside the display box, else ``None``.
    """
    if logo is None:
        return None
    bitmap = wx.Bitmap(str(logo), wx.BITMAP_TYPE_PNG)
    if not bitmap.IsOk():
        return None
    image = bitmap.ConvertToImage()
    width, height = image.GetWidth(), image.GetHeight()
    fitted = team_editor.logo_fit_size(width, height, within=RIDE_LOGO_DISPLAY_SIZE)
    if fitted == (width, height):
        return bitmap
    return wx.Bitmap(image.Rescale(*fitted, wx.IMAGE_QUALITY_HIGH))


def _format_count(value: int) -> str:
    """Render *value* with a space as the thousands separator.

    Matches the canvas exactly: 1124 -> "1 124", 42 -> "42".
    """
    return f"{value:,}".replace(",", " ")


def _start_blocked_size(fitted: tuple[int, int]) -> tuple[int, int]:
    """Return the blocked-start dialog size from its fitted one.

    ``start_blocked_dlg`` is loaded with no code-side min-size, so it
    would otherwise open at the issue list's narrow best size and clip
    the issue text. Doubling both dimensions gives the dialog's single
    (and therefore stretched) "Issue" column room, mirroring
    ``CsvPreviewDialog._apply_min_size``'s Fit-then-scale idiom.

    Args:
        fitted: The ``(width, height)`` ``Fit()`` measured.

    Returns:
        ``(width * 2, height * 2)``.
    """
    return (fitted[0] * 2, fitted[1] * 2)
