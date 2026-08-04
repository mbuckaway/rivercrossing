# SPDX-License-Identifier: GPL-3.0-only
"""``main_frame``: the console (1a), wired to its demo/live DataSource.

xrc-windows.md section A's code-side footnote lists six things
``main.xrc`` cannot express: the crossings feed's DataView columns
and per-row attributes, the card imagelist, the three ``wxInfoBar``
shells, the ``main_splitter`` sash restore, per-state menu enabling,
and ``SetAppearance``. This module covers the first four for
``main_frame`` -- per-state menu enabling is ``commands.py``'s route
table (E1.4) and ``SetAppearance`` is a later theme task; neither is
in this task's file batch.

:class:`MainFrame` decorates an already-XRC-loaded ``wx.Frame`` -- it
never calls ``LoadFrame`` itself. Loading stays the caller's job
(``harness.load_window`` in tests, the app bootstrap in production),
matching every other window in this codebase and the rule this
repo's own harness states: reuse the one loader, never build a
second.

**Why no separate ``console_panel.py`` (SIMPLECODE Rule 7 -- one
module until a split earns its keep):** module-skeletons.md names
one for "feed, entry field, counters", but ``main.xrc`` never splits
those controls into their own XRC panel resource -- they are plain
children of this one frame, alongside the InfoBars, splitter and
statusbar this module already owns. Two Python files sharing one XRC
window and one set of ``FindWindowByName`` calls would be a
same-window split with no separable XRC boundary behind it, the
paper-cut kind Rule 7 warns against. If a second real window ever
needs the feed-rendering logic, extract it then.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.dataview

from rivercrossing.ride import RideStatus
from rivercrossing.ui import feed_model, ids
from rivercrossing.ui.views._support import default_card_images, find_control

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.ui.cards_imagelist import CardImageList
    from rivercrossing.ui.presenters.console import Cue
    from rivercrossing.ui.presenters.data_source import Counters, DataSource, FeedRow

__all__ = [
    "FINISHED_INFOBAR",
    "MIN_SIZE",
    "REOPENED_INFOBAR",
    "RESUME_INFOBAR",
    "CrossingsFeedModel",
    "MainFrame",
]

_TEXT_ACCESSORS: tuple[Callable[[FeedRow], str], ...] = (
    lambda row: row.time,
    lambda row: row.plate,
    lambda row: row.entry,
    lambda row: str(row.lap),
    lambda row: row.lap_time,
    lambda row: row.total,
)

# ui/ids.py is generated from the .xrc files (R-05); these three
# names never appear there since XRC cannot author a wxInfoBar at
# all (xrc-windows.md's own code-side footnote, main.xrc's header).
RESUME_INFOBAR = "resume_infobar"
REOPENED_INFOBAR = "reopened_infobar"
FINISHED_INFOBAR = "finished_infobar"

# xrc-windows.md section A: "Min frame 1100x700, fits 1366x768."
# XRC has no window-level minsize property (main.xrc's own header) --
# only <size>, which sets the *initial* size, not the floor.
MIN_SIZE = (1100, 700)

# Interim, process-lifetime sash memory. Task E8.1.1 ("all settings
# survive relaunch") replaces this with the real, disk-backed
# settings store; nothing else in this module depends on how that
# eventually works, and nothing outside this module reads it.
_persisted_main_splitter_sash: int | None = None


class CrossingsFeedModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over ``FeedRow`` rows for ``crossings_list``.

    ``# type: ignore[misc]``: wx ships no stubs (pyproject.toml's
    ``ignore_missing_imports`` for ``wx.*``), so every wx member --
    ``DataViewIndexListModel`` included -- resolves to ``Any``, and
    mypy refuses to subclass ``Any``. Unavoidable for the first wx
    base class this codebase subclasses; nothing to fix here.

    The wx-facing half of the crossings feed; ``ui/feed_model.py``
    holds the column layout and the two decisions this class
    delegates to (``card_asset_key_or_none``, the flagged-row
    lookup), so those stay testable without ``wx``
    (``tests/unit/ui/test_feed_model.py``). This class has exactly
    one consumer, :class:`MainFrame`, which is why it lives here
    rather than in its own file (SIMPLECODE Rule 7).

    Rows are supplied once at construction -- :class:`MainFrame`
    builds a fresh model each time ``show_feed`` runs rather than
    mutating this one in place, which keeps the 30-row cap (R-32)
    trivial and avoids ``DataViewIndexListModel``'s row-count-change
    notifications entirely.
    """

    def __init__(self, rows: Sequence[FeedRow], card_images: CardImageList) -> None:
        """Wrap *rows*, newest first; render cards via *card_images*."""
        super().__init__(len(rows))
        self._rows = tuple(rows)
        self._card_images = card_images
        self._flagged = feed_model.flagged_row_indexes(self._rows)

    def GetColumnCount(self) -> int:
        """Return the feed's fixed seven columns."""
        return len(feed_model.COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:
        """Return each column's wx variant type.

        ``"wxBitmap"`` for the card column -- see :class:`MainFrame`'s
        ``_build_columns`` for why not the newer ``"wxBitmapBundle"``.
        """
        return "wxBitmap" if col == feed_model.COL_CARD else "string"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the cell value at *row*/*col*."""
        feed_row = self._rows[row]
        if col == feed_model.COL_CARD:
            return self._card_bitmap(feed_row.card)
        return _TEXT_ACCESSORS[col](feed_row)

    def GetAttrByRow(self, row: int, col: int, attr: Any) -> bool:  # noqa: ANN401, ARG002
        """Bold the whole row when its crossing is flagged (R-34).

        *col* is unused: xrc-windows.md's code-side note bolds the
        entire flagged row, not one cell.
        """
        if row not in self._flagged:
            return False
        attr.SetBold(True)  # noqa: FBT003 -- wx API takes a positional bool
        return True

    def _card_bitmap(self, card: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the dealt card's bitmap, or a blank cell if held."""
        key = feed_model.card_asset_key_or_none(card)
        if key is None:
            return wx.NullBitmap
        return self._card_images.bitmap(key)


class MainFrame:
    """Code-side behaviour for ``main_frame`` (the console, 1a).

    Implements ``ConsoleView`` (module-skeletons.md's presenter
    contract) plus one extra method, :meth:`set_hide_times`, the
    Protocol does not yet declare: ``ConsolePresenter.on_hide_times``
    is still a no-op stub, so nothing calls a view-side hide-times
    method through the Protocol yet. A later phase adds that member
    once the presenter actually calls it.
    """

    def __init__(
        self,
        frame: wx.Frame,
        *,
        data_source: DataSource,
        card_images: CardImageList | None = None,
    ) -> None:
        """Decorate an already-loaded ``main_frame`` window.

        Args:
            frame: The ``wx.Frame`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``main.xrc``.
            data_source: The display-data seam (module-skeletons.md
                ``ui.presenters``). This view knows only the
                :class:`~rivercrossing.ui.presenters.data_source.
                DataSource` Protocol -- the caller wires in whichever
                implementation applies (``DemoDataSource`` today, a
                store-backed one from EPIC 4-5 on).
            card_images: The card bitmaps for the feed's Card column;
                defaults to the packaged deck at 1x.
        """
        self.frame = frame
        self.data_source = data_source
        self.card_images = card_images if card_images is not None else default_card_images()

        self.crossings_list = self._find(ids.CROSSINGS_LIST, wx.dataview.DataViewCtrl)
        self.main_splitter = self._find(ids.MAIN_SPLITTER, wx.SplitterWindow)
        self.plate_input = self._find(ids.PLATE_INPUT, wx.TextCtrl)
        self.record_btn = self._find(ids.RECORD_BTN, wx.Button)
        self.last_crossing_lbl = self._find(ids.LAST_CROSSING_LBL, wx.StaticText)
        self.ride_status_lbl = self._find(ids.RIDE_STATUS_LBL, wx.StaticText)
        self.crossings_count_lbl = self._find(ids.CROSSINGS_COUNT_LBL, wx.StaticText)
        self.cards_count_lbl = self._find(ids.CARDS_COUNT_LBL, wx.StaticText)
        self.on_course_lbl = self._find(ids.ON_COURSE_LBL, wx.StaticText)
        self.shoe_lbl = self._find(ids.SHOE_LBL, wx.StaticText)

        # LoadFrame does not honour main.xrc's <size> -- measured: the
        # frame comes back sized to the sizer's own computed minimum
        # (~429x373), not the canvas's 1100x700. SetMinSize alone only
        # stops *future* shrinking below the floor; SetSize is what
        # actually grows this window (and the splitter's client area)
        # to the canvas's documented size right now.
        self.frame.SetMinSize(wx.Size(*MIN_SIZE))
        self.frame.SetSize(wx.Size(*MIN_SIZE))

        self._next_infobar_slot = 1  # main.xrc's spacer placeholder sits at index 0
        self.resume_infobar = self._build_infobar(RESUME_INFOBAR)
        self.reopened_infobar = self._build_infobar(REOPENED_INFOBAR)
        self.finished_infobar = self._build_infobar(FINISHED_INFOBAR)

        self._hideable_columns = self._build_columns()
        self._crossings_model: CrossingsFeedModel | None = None

        # Reflow now that the size and every sizer item are final, so
        # the splitter has its real client area before a sash position
        # is read or restored.
        self.frame.Layout()

        self.main_splitter.Bind(wx.EVT_SPLITTER_SASH_POS_CHANGED, self._on_sash_changed)
        self._restore_sash_position()

        self.show_feed(self.data_source.feed_rows())
        self.show_counters(self.data_source.counters())

    # ------------------------------------------------------- lookups

    def _find(self, name: str, expected_type: type = wx.Window) -> Any:  # noqa: ANN401
        """Resolve one of this frame's own child controls by name.

        See :func:`find_control`'s docstring (``ui.views._support``)
        for the full measured reasoning this mirrors: an explicit
        ``self.frame`` parent scopes the lookup, and the retry loop
        settles the address-reuse hazard this wx build exhibits
        under sustained window churn.

        Raises:
            LookupError: If *name* does not resolve to an
                *expected_type* instance inside this frame, even
                after settling.
        """
        return find_control(self.frame, name, expected_type)

    # ------------------------------------------------------- InfoBars

    def _build_infobar(self, name: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Build one code-side InfoBar and insert it after the spacer.

        ``main.xrc``'s spacer placeholder sits at sizer index 0; each
        InfoBar is inserted right after it (and after any InfoBar
        already inserted), so the three stack in call order. A fresh
        ``wx.InfoBar`` starts hidden (measured) -- nothing further is
        needed for R-73's "hidden by default".
        """
        bar = wx.InfoBar(self.frame)
        bar.SetName(name)
        self.frame.GetSizer().Insert(self._next_infobar_slot, bar, 0, wx.EXPAND)
        self._next_infobar_slot += 1
        return bar

    # ------------------------------------------------------- columns

    def _build_columns(self) -> tuple[Any, ...]:
        """Append the feed's seven columns in canvas order.

        Returns:
            The hide-times-affected columns (Lap time, Total), in
            column order, for :meth:`set_hide_times` to toggle.

        The Card column uses an explicit
        ``DataViewBitmapRenderer("wxBitmap")`` rather than
        ``AppendBitmapColumn``'s default: this wx build (4.3.1 /
        wxWidgets 3.3.3, probed with a throwaway script per this
        repo's convention) registers that default renderer against
        ``"wxBitmapBundle"``, which silently drops a plain
        ``wx.Bitmap`` value. The explicit ``"wxBitmap"`` renderer
        accepts ``CardImageList.bitmap()``'s values unchanged.
        """
        hideable = []
        for col, label in enumerate(feed_model.COLUMN_LABELS):
            if col == feed_model.COL_CARD:
                renderer = wx.dataview.DataViewBitmapRenderer("wxBitmap")
                column = wx.dataview.DataViewColumn(label, renderer, col)
                self.crossings_list.AppendColumn(column)
            else:
                column = self.crossings_list.AppendTextColumn(label, col)
            if col in feed_model.TIME_COLUMNS:
                hideable.append(column)
        return tuple(hideable)

    def set_hide_times(self, *, hide: bool) -> None:
        """Toggle the Lap time/Total columns per R-37.

        The clock (``clock_elapsed_lbl``/``clock_remaining_lbl``) is
        untouched -- R-37 keeps it visible regardless of this setting.
        """
        for column in self._hideable_columns:
            column.SetHidden(hide)

    # -------------------------------------------------------- splitter

    def _restore_sash_position(self) -> None:
        """Apply any sash position already saved this process."""
        if _persisted_main_splitter_sash is not None:
            self.main_splitter.SetSashPosition(_persisted_main_splitter_sash)

    def persist_layout(self) -> None:
        """Save ``main_splitter``'s current sash position.

        Bound to the sash-changed event for real dragging; wx only
        fires that event from genuine user drag input, never from a
        programmatic ``SetSashPosition`` (measured), so tests call
        this directly instead (CODINGSTANDARDS-UX-DESKTOP.md section
        6: sash positions must persist across restarts).
        """
        global _persisted_main_splitter_sash  # noqa: PLW0603 -- see module docstring
        _persisted_main_splitter_sash = self.main_splitter.GetSashPosition()

    def _on_sash_changed(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Persist a real, user-driven sash drag."""
        event.Skip()
        self.persist_layout()

    # ----------------------------------------------------- ConsoleView

    def show_feed(self, rows: list[FeedRow]) -> None:
        """Render the crossings feed, newest first (ConsoleView)."""
        self._crossings_model = CrossingsFeedModel(rows, self.card_images)
        self.crossings_list.AssociateModel(self._crossings_model)

    def show_counters(self, c: Counters) -> None:
        """Render the four counter chips (ConsoleView)."""
        self.crossings_count_lbl.SetLabel(_format_count(c.crossings))
        self.cards_count_lbl.SetLabel(_format_count(c.cards_dealt))
        self.on_course_lbl.SetLabel(_format_count(c.on_course))
        self.shoe_lbl.SetLabel(f"{c.shoe_remaining}/{c.shoe_total}")

    def flash_crossing(self, r: FeedRow) -> None:
        """Highlight the just-recorded crossing (ConsoleView)."""
        self.last_crossing_lbl.SetLabel(
            f"✓ {r.plate} · {r.entry} · Lap {r.lap} · {r.lap_time} · dealt {r.card}"
        )

    def set_state(self, status: RideStatus) -> None:
        """Reflect the ride's lifecycle state (ConsoleView).

        Minimal for this task: the status label and record-crossing
        row enablement (A4: ``record_btn`` tracks ``plate_input``,
        both live only in RUNNING). The DRAFT/FINISHED/REOPENED
        banner variants xrc-windows.md's footnote lists are a later
        phase's job.
        """
        self.ride_status_lbl.SetLabel(status.value.upper())
        running = status == RideStatus.RUNNING
        self.plate_input.Enable(running)
        self.record_btn.Enable(running)

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
        """

        def _submit(_event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
            on_submit(self.plate_input.GetValue())

        self.plate_input.Bind(wx.EVT_TEXT_ENTER, _submit)
        self.record_btn.Bind(wx.EVT_BUTTON, _submit)

    def play(self, cue: Cue) -> None:
        """Play the audio cue for the given event (ConsoleView).

        Stub: ``ui.sound`` (module-skeletons.md) lands in E4.4.3 and
        wires real playback; nothing calls this yet outside tests.
        """


def _format_count(value: int) -> str:
    """Render *value* with a space as the thousands separator.

    Matches the canvas exactly: 1124 -> "1 124", 42 -> "42".
    """
    return f"{value:,}".replace(",", " ")
