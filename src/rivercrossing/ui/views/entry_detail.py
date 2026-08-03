# SPDX-License-Identifier: GPL-3.0-only
"""``EntryDetailDialog``: ``entry_detail_dlg`` (1e), one entry (E1.5.2).

xrc-windows.md section C's code-side footnote puts
``entry_header_lbl``/``members_lbl`` text, ``cards_list`` and
``laps_list``'s rows, and the card bitmaps both draw, in code --
``detail.xrc``'s own header explains why (``wxDataViewListCtrl``
would overwrite the frozen names). This module is that binding.

**D15** (already decided, implemented here without relitigating):
the canvas annotates ``cards_list`` "icon mode", but no such mode
exists on a ``wxDataViewCtrl`` (icon view is a ``wxListCtrl``
feature) -- both ``cards_list`` and ``laps_list``'s Card columns use
an explicit ``wx.dataview.DataViewBitmapRenderer("wxBitmap")``,
copying ``views/main_frame.py``'s ``CrossingsFeedModel`` column,
whose own docstring records the measured trap this avoids:
``AppendBitmapColumn``'s default renderer registers against
``"wxBitmapBundle"`` and silently drops a plain ``wx.Bitmap``.

**D16**: xrc-windows.md states no drawn width for this dialog, so
none is invented here -- both DataViews instead carry an explicit,
content-derived column width, so ``Fit()`` computes a genuinely
non-degenerate floor. See this task's own report for the measured
result and how the six-button action row, not the DataView content,
turned out to dominate it.

The demo fixture's own comment (``rivercrossing.demo``) records that
``cards_held`` only carries 5 of the entry's 9 dealt cards -- the
rest are illegible in the canvas and are not invented here either;
``show_entry`` renders exactly what the fixture gives it.

``_find`` and the card-imagelist cache are now shared via
``ui.views._support`` -- see that module's docstring for why they
used to be duplicated here.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.dataview

from rivercrossing.ui import ids
from rivercrossing.ui.cards_imagelist import CardImageList, asset_key
from rivercrossing.ui.views._support import associate_model, default_card_images, find_control

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.ui.presenters.data_source import DataSource, EntryDetail, EntryLapRow

__all__ = [
    "CARDS_HELD_COLUMN_WIDTH",
    "COL_CARD",
    "COL_LAP",
    "COL_LAP_TIME",
    "COL_RIDER",
    "COL_TIME",
    "LAPS_COLUMN_WIDTHS",
    "LAP_COLUMN_LABELS",
    "MIN_SIZE",
    "CardsHeldModel",
    "EntryDetailDialog",
    "EntryLapsModel",
]

COL_LAP = 0
COL_TIME = 1
COL_LAP_TIME = 2
COL_RIDER = 3
COL_CARD = 4

# xrc-windows.md C's exact order: Lap | Time | Lap time | Rider | Card.
LAP_COLUMN_LABELS: tuple[str, ...] = ("Lap", "Time", "Lap time", "Rider", "Card")

# D16: explicit, content-derived widths -- a 24x32 card face plus a
# little padding for the bitmap column, and enough for the widest
# demo value ("14:22:18", "3:02:11"-shaped strings) in each text one.
CARDS_HELD_COLUMN_WIDTH = 60
LAPS_COLUMN_WIDTHS: tuple[int, ...] = (50, 80, 80, 60, 60)

# D16: measured via Fit() with the demo rows loaded and the widths
# above applied -- the dialog's own edit_crossing_btn..audit_btn row
# turned out to be the natural floor, not the DataViews (this task's
# own report). Not a canvas number: xrc-windows.md states none.
MIN_SIZE = (726, 331)


class CardsHeldModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over held card codes for ``cards_list``.

    ``# type: ignore[misc]``: wx ships no stubs, so mypy refuses to
    subclass ``Any`` -- the same unavoidable annotation
    ``CrossingsFeedModel`` carries in ``views/main_frame.py``.
    """

    def __init__(self, cards_held: Sequence[str], card_images: CardImageList) -> None:
        """Wrap *cards_held*, rendered via *card_images* (D15)."""
        super().__init__(len(cards_held))
        self._cards_held = tuple(cards_held)
        self._card_images = card_images

    def GetColumnCount(self) -> int:
        """Return one column: the card bitmap (D15)."""
        return 1

    def GetColumnType(self, col: int) -> str:  # noqa: ARG002 -- only one column exists
        """Return "wxBitmap" (D15's explicit renderer type)."""
        return "wxBitmap"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401, ARG002
        """Return the held card's bitmap at *row*."""
        return self._card_images.bitmap(asset_key(self._cards_held[row]))


_TEXT_ACCESSORS: tuple[Callable[[EntryLapRow], str], ...] = (
    lambda lap_row: str(lap_row.lap),
    lambda lap_row: lap_row.time,
    lambda lap_row: lap_row.lap_time,
    lambda lap_row: lap_row.rider,
)


class EntryLapsModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over ``EntryLapRow`` rows for ``laps_list``."""

    def __init__(self, rows: Sequence[EntryLapRow], card_images: CardImageList) -> None:
        """Wrap *rows*; render Card cells via *card_images* (D15)."""
        super().__init__(len(rows))
        self._rows = tuple(rows)
        self._card_images = card_images

    def GetColumnCount(self) -> int:
        """Return the lap history's fixed five columns."""
        return len(LAP_COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:
        """Return "wxBitmap" for the Card column, "string" otherwise."""
        return "wxBitmap" if col == COL_CARD else "string"

    def _card_bitmap(self, card: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the lap's dealt card bitmap."""
        return self._card_images.bitmap(asset_key(card))

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the cell value at *row*/*col*."""
        lap_row = self._rows[row]
        if col == COL_CARD:
            return self._card_bitmap(lap_row.card)
        return _TEXT_ACCESSORS[col](lap_row)


class EntryDetailDialog:
    """Code-side behaviour for ``entry_detail_dlg`` (1e).

    Named ``EntryDetailDialog``, not ``EntryDetail``, to avoid
    colliding with ``rivercrossing.ui.presenters.data_source.
    EntryDetail``, the view-model this class renders.

    Implements ``DetailView.show_entry`` (module-skeletons.md's
    presenter contract); ``move_rider_btn``'s pooled-only enablement
    and the edit/deal/void/DNF actions are a later phase's job per
    ``DetailPresenter``'s own docstring ("Phase 5 wires...") and are
    not in this task's scope.
    """

    def __init__(
        self,
        dialog: wx.Dialog,
        plate: str,
        *,
        data_source: DataSource,
    ) -> None:
        """Decorate an already-loaded ``entry_detail_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``detail.xrc``.
            plate: The plate of the entry to render.
            data_source: The display-data seam. This view knows only
                the :class:`~rivercrossing.ui.presenters.data_source.
                DataSource` Protocol -- the caller wires in whichever
                implementation applies.

        No ``card_images=`` override: unlike ``MainFrame``, nothing in
        this task needs one (SIMPLECODE Rule 1) -- both DataViews'
        Card columns always render this dialog's own private deck
        (module docstring).
        """
        self.dialog = dialog
        self.data_source = data_source
        self.card_images = default_card_images()

        self.entry_header_lbl = self._find(ids.ENTRY_HEADER_LBL, wx.StaticText)
        self.members_lbl = self._find(ids.MEMBERS_LBL, wx.StaticText)
        self.cards_list = self._find(ids.CARDS_LIST, wx.dataview.DataViewCtrl)
        self.laps_list = self._find(ids.LAPS_LIST, wx.dataview.DataViewCtrl)
        self._build_cards_column()
        self._build_laps_columns()
        self._cards_model: CardsHeldModel | None = None
        self._laps_model: EntryLapsModel | None = None

        self.show_entry(self.data_source.entry_detail(plate))
        self._apply_min_size()

    def _find(self, name: str, expected_type: type = wx.Window) -> Any:  # noqa: ANN401
        """Resolve one of this dialog's own child controls by name.

        See :func:`find_control`'s docstring (``ui.views._support``)
        for the full measured reasoning this mirrors.

        Raises:
            LookupError: If *name* does not resolve to an
                *expected_type* instance inside this dialog, even
                after settling.
        """
        return find_control(self.dialog, name, expected_type)

    def _build_cards_column(self) -> None:
        """Append ``cards_list``'s one bitmap column (D15)."""
        renderer = wx.dataview.DataViewBitmapRenderer("wxBitmap")
        column = wx.dataview.DataViewColumn("Card", renderer, 0, width=CARDS_HELD_COLUMN_WIDTH)
        self.cards_list.AppendColumn(column)

    def _build_laps_columns(self) -> None:
        """Append ``laps_list``'s 5 columns, Card as a bitmap (D15)."""
        labelled_widths = zip(LAP_COLUMN_LABELS, LAPS_COLUMN_WIDTHS, strict=True)
        for col, (label, width) in enumerate(labelled_widths):
            if col == COL_CARD:
                renderer = wx.dataview.DataViewBitmapRenderer("wxBitmap")
                column = wx.dataview.DataViewColumn(label, renderer, col, width=width)
                self.laps_list.AppendColumn(column)
            else:
                self.laps_list.AppendTextColumn(label, col, width=width)

    def show_entry(self, detail: EntryDetail) -> None:
        """Render header, members, held cards and laps rows.

        See ``ui.views._support.associate_model``'s docstring for
        why each DataView repaints explicitly (unverified remedy).
        """
        self.entry_header_lbl.SetLabel(detail.header)
        self.members_lbl.SetLabel(detail.members)
        self._cards_model = CardsHeldModel(detail.cards_held, self.card_images)
        associate_model(self.cards_list, self._cards_model)
        self._laps_model = EntryLapsModel(detail.laps, self.card_images)
        associate_model(self.laps_list, self._laps_model)

    def _apply_min_size(self) -> None:
        """``Fit()`` the dialog to its now content-bearing sizer (D16).

        No canvas width is stated for this dialog, so nothing is
        forced beforehand -- the explicit column widths already
        applied are what keeps this floor meaningful rather than
        collapsing to the near-empty-DataView minimum D16 warns
        about.
        """
        self.dialog.Fit()
        self.dialog.SetMinSize(self.dialog.GetSize())
