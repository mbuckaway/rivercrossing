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

The test-only fixture's own comment (``rivercrossing.demo``) records
that its ``cards_held`` only carries 5 of the entry's 9 dealt cards --
the rest are illegible in the canvas and are not invented there;
``show_entry`` renders exactly what the source gives it. E5.4.2 wired
the app's entry-detail route to ``EmptyDataSource`` (no store-backed
entry selected yet, E7 wires the real lookup), so the dialog opens
with an empty header/members/cards/laps until then.

``_find`` and the card-imagelist cache are now shared via
``ui.views._support`` -- see that module's docstring for why they
used to be duplicated here.

E7.2.1 wires the six action buttons (edit crossing / deal card / void
card / move rider / mark DNF / audit trail) through
:class:`~rivercrossing.ui.presenters.detail.DetailPresenter`, which
this class constructs the same way ``RideSetup``/``ResultsWindow``
build their own presenters. The view implements the grown
``DetailView`` surface: it tracks the ``laps_list`` selection
(``selected_lap``), opens each correction dialog through
``ui.views.corrections``' shared runners (returning the confirmed
request or None), and forwards notices to the app's status bar via
the optional ``notify`` seam. The optional ``engine``/``roster`` are
the live write sides (None in the no-store empty state, where the
buttons post a notice instead of acting); ``resource`` is the XRC
resource the sub-dialogs load from; ``on_corrected`` fires after a
successful correction so the app bootstrap can refresh its menu
enablement. The pre-E7 contract is preserved: constructing with a
plate no entry owns raises ``LookupError`` (R-38's loud failure,
pinned by ``test_lists_entry_detail``).
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.dataview

from rivercrossing.ui import ids
from rivercrossing.ui.cards_imagelist import CardImageList, asset_key
from rivercrossing.ui.presenters.detail import (
    CardVoid,
    CrossingEdit,
    DetailPresenter,
    DnfMark,
    ManualDeal,
    RiderMove,
)
from rivercrossing.ui.views import corrections
from rivercrossing.ui.views._support import associate_model, default_card_images, find_control

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.ride import RideEngine
    from rivercrossing.roster import Roster
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

# D16: measured via Fit() with the demo rows loaded and the
# widths above applied -- the dialog's own edit_crossing_btn..
# audit_btn row turned out to be the natural floor on macOS,
# not the DataViews (this task's own report). Not a canvas
# number: xrc-windows.md states none. windows-latest CI
# measured a narrower 650px natural floor there (Segoe UI's
# own button metrics), so the width below is now forced before
# Fit(), matching ride_library/rider_editor/results_win, for
# cross-platform determinism.
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

    Implements the ``DetailView`` presenter contract
    (module-skeletons.md) in full (E7.2.1): ``show_entry``,
    ``set_move_rider_enabled``, the ``laps_list`` selection channel
    (``selected_lap``), the six correction-dialog openers, and the
    notice channel. The one live presenter is built here, the same
    ``RideSetup``/``ResultsWindow`` precedent.
    """

    def __init__(  # noqa: PLR0913 -- (dialog, plate, data_source) + the four optional E7 seams
        self,
        dialog: wx.Dialog,
        plate: str,
        *,
        data_source: DataSource,
        engine: RideEngine | None = None,
        roster: Roster | None = None,
        resource: Any | None = None,  # noqa: ANN401 -- wx ships no stubs
        notify: Callable[[str], None] | None = None,
        on_corrected: Callable[[], None] | None = None,
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
            engine: The live ride engine (the corrections write side),
                threaded to the presenter; ``None`` in the E5.4.2
                empty state, where the correction buttons post a
                notice instead of acting.
            roster: The live roster (the pooled-move write side and
                the entry-label source); ``None`` in the empty state.
            resource: The loaded ``wx.xrc.XmlResource`` the correction
                sub-dialogs load from; ``None`` only in constructions
                that never open one.
            notify: Posts notices to the app's status bar (wired by
                the bootstrap); ``None`` in direct constructions.
            on_corrected: Fires after a successful correction so the
                bootstrap can refresh its menu enablement.

        Raises:
            LookupError: If no entry owns *plate* -- R-38's loud
                failure, pinned by the functional suite.
        """
        self.dialog = dialog
        self.data_source = data_source
        self.plate = plate
        self.card_images = default_card_images()
        self._resource = resource
        self._engine = engine
        self._roster = roster
        self._notify = notify

        self.entry_header_lbl = self._find(ids.ENTRY_HEADER_LBL, wx.StaticText)
        self.members_lbl = self._find(ids.MEMBERS_LBL, wx.StaticText)
        self.cards_list = self._find(ids.CARDS_LIST, wx.dataview.DataViewCtrl)
        self.laps_list = self._find(ids.LAPS_LIST, wx.dataview.DataViewCtrl)
        self._build_cards_column()
        self._build_laps_columns()
        self._cards_model: CardsHeldModel | None = None
        self._laps_model: EntryLapsModel | None = None
        self._laps_rows: tuple[EntryLapRow, ...] = ()
        self._selected_row: int | None = None

        # E7.2.1: the six action buttons, resolved once and bound to
        # the presenter (the same wire-to-presenter idiom the console
        # uses); move_rider_btn starts disabled until the presenter's
        # pooled-only rule says otherwise.
        self.edit_crossing_btn = self._find(ids.EDIT_CROSSING_BTN, wx.Button)
        self.deal_card_btn = self._find(ids.DEAL_CARD_BTN, wx.Button)
        self.void_card_btn = self._find(ids.VOID_CARD_BTN, wx.Button)
        self.move_rider_btn = self._find(ids.MOVE_RIDER_BTN, wx.Button)
        self.dnf_btn = self._find(ids.DNF_BTN, wx.Button)
        self.audit_btn = self._find(ids.AUDIT_BTN, wx.Button)

        self.presenter = DetailPresenter(
            self,
            data_source,
            plate=plate,
            engine=engine,
            roster=roster,
            on_corrected=on_corrected,
        )
        self._bind_actions()

        # Render once: an unknown plate raises here (R-38), preserving
        # the pre-E7 construction contract.
        self.show_entry(self.data_source.entry_detail(plate))
        self.set_move_rider_enabled(enabled=self.presenter.move_rider_enabled())
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
        A fresh render clears the lap selection: the operator must
        pick a row again before the edit/void buttons act on one.
        """
        self.entry_header_lbl.SetLabel(detail.header)
        self.members_lbl.SetLabel(detail.members)
        self._cards_model = CardsHeldModel(detail.cards_held, self.card_images)
        associate_model(self.cards_list, self._cards_model)
        self._laps_model = EntryLapsModel(detail.laps, self.card_images)
        associate_model(self.laps_list, self._laps_model)
        self._laps_rows = detail.laps
        self._selected_row = None

    def _apply_min_size(self) -> None:
        """Force the measured 726px floor, then ``Fit()`` (D16).

        No canvas width is stated for this dialog, so 726px is
        not a canvas number -- it is this task's own macOS
        measurement (module docstring). windows-latest CI
        measured a narrower 650px natural floor there (Segoe
        UI's own button metrics), so the width is now forced
        beforehand, exactly like ride_library/rider_editor/
        results_win already force theirs, for cross-platform
        determinism.
        """
        self.dialog.SetMinSize(wx.Size(MIN_SIZE[0], -1))
        self.dialog.Fit()
        self.dialog.SetMinSize(self.dialog.GetSize())

    # ---------------------------------------------- E7.2.1 actions

    def _bind_actions(self) -> None:
        """Bind the six action buttons and the laps selection.

        Each button forwards straight to the presenter (the console's
        ``wire_entry``/``wire_console`` idiom); the laps_list
        selection is tracked here so ``selected_lap`` can feed the
        edit/void flows.
        """
        self.dialog.Bind(
            wx.EVT_BUTTON,
            lambda _event: self.presenter.on_edit_crossing_clicked(),
            self.edit_crossing_btn,
        )
        self.dialog.Bind(
            wx.EVT_BUTTON,
            lambda _event: self.presenter.on_deal_card_clicked(),
            self.deal_card_btn,
        )
        self.dialog.Bind(
            wx.EVT_BUTTON,
            lambda _event: self.presenter.on_void_card_clicked(),
            self.void_card_btn,
        )
        self.dialog.Bind(
            wx.EVT_BUTTON,
            lambda _event: self.presenter.on_move_rider_clicked(),
            self.move_rider_btn,
        )
        self.dialog.Bind(
            wx.EVT_BUTTON,
            lambda _event: self.presenter.on_dnf_clicked(),
            self.dnf_btn,
        )
        self.dialog.Bind(
            wx.EVT_BUTTON,
            lambda _event: self.presenter.on_audit_clicked(),
            self.audit_btn,
        )
        self.laps_list.Bind(wx.dataview.EVT_DATAVIEW_SELECTION_CHANGED, self._on_lap_selected)

    def _on_lap_selected(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Record the laps_list row the operator selected."""
        item = event.GetItem()
        # The model's GetRow maps a valid item back to its index
        # (wxDataViewCtrl has no ItemToRow on this build -- the
        # ride_library precedent).
        row = int(self.laps_list.GetModel().GetRow(item)) if item.IsOk() else -1
        self._selected_row = row if row >= 0 else None
        event.Skip()

    # -------------------------------------------------- DetailView

    def set_move_rider_enabled(self, *, enabled: bool) -> None:
        """Enable move_rider_btn only for rider-pooled team entries."""
        self.move_rider_btn.Enable(enabled)

    def selected_lap(self) -> EntryLapRow | None:
        """Return the selected laps_list row, or None.

        The edit/void buttons need one concrete crossing; the row
        carries the lap number (the engine's seq), the recorded time
        and the dealt card the flows act on.
        """
        if self._selected_row is None:
            return None
        if 0 <= self._selected_row < len(self._laps_rows):
            return self._laps_rows[self._selected_row]
        return None

    def show_edit_crossing(self, *, adding: bool, plate: str, time: str) -> CrossingEdit | None:
        """Open edit_crossing_dlg (the shared corrections runner).

        The selected lap supplies the crossing's seq when editing; add
        mode leaves it to the caller (the menu flow).
        """
        if self._resource is None:
            self.show_notice("Edit crossing unavailable")
            return None
        lap = self.selected_lap()
        seq = lap.lap if lap is not None else None
        base = self._engine.config.event_date if self._engine is not None else None
        return corrections.run_edit_crossing(
            self._resource,
            frame=self.dialog,
            adding=adding,
            plate=plate,
            time=time,
            seq=seq,
            base_date=base,
        )

    def open_manual_deal(self, *, plate: str) -> ManualDeal | None:
        """Open manual_deal_dlg (the shared corrections runner)."""
        if self._resource is None:
            self.show_notice("Deal card unavailable")
            return None
        return corrections.run_manual_deal(self._resource, frame=self.dialog, plate=plate)

    def open_void_card(self, *, card: str, entry: str) -> CardVoid | None:
        """Open void_card_confirm_dlg naming the card + entry."""
        if self._resource is None:
            self.show_notice("Void card unavailable")
            return None
        return corrections.run_void_card(
            self._resource,
            frame=self.dialog,
            entry_id=self.plate,
            card=card,
            entry=entry,
        )

    def open_dnf(self, *, entry: str) -> DnfMark | None:
        """Open dnf_confirm_dlg naming the entry."""
        if self._resource is None:
            self.show_notice("Mark DNF unavailable")
            return None
        return corrections.run_dnf(
            self._resource,
            frame=self.dialog,
            entry_id=self.plate,
            entry=entry,
        )

    def open_move_rider(
        self, *, riders: tuple[str, ...], teams: tuple[str, ...]
    ) -> RiderMove | None:
        """Open the code-built team picker."""
        return corrections.run_move_rider(self.dialog, riders=riders, teams=teams)

    def open_audit(self) -> None:
        """Open audit_dlg pre-filtered to this entry (R-38, E7.3.1).

        The E7.2.1 plain open becomes the deep-link: the viewer opens
        with ``audit_search`` pre-set to this entry's plate and the
        list narrowed to it, over the same live display source and
        roster the detail dialog already holds.
        """
        if self._resource is None:
            self.show_notice("Audit trail unavailable")
            return
        corrections.run_audit(
            self._resource,
            frame=self.dialog,
            data_source=self.data_source,
            roster=self._roster,
            entry_filter=self.plate,
        )

    def show_notice(self, text: str) -> None:
        """Post *text* through the app's status-bar seam (DetailView).

        A direct construction with no ``notify`` seam is a no-op --
        the notice channel exists for the live app, and the presenter
        unit suite asserts notices against its own fake view.
        """
        if self._notify is not None:
            self._notify(text)
