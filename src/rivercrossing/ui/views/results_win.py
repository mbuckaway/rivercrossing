# SPDX-License-Identifier: GPL-3.0-only
"""``ResultsWindow``: results_frame (1f), standings (E1.5.2/E6.4.1).

xrc-windows.md section D's code-side footnote puts
``standings_list``'s columns and rows in code -- ``results.xrc``'s
own header explains why (``wxDataViewListCtrl`` would overwrite the
frozen name). This module is that binding.

The canvas's "Best 5" cell is plain text carrying suit glyphs ("K♠
K♣ K♦ JK★ 9♥"), confirmed against ``design/docs-html``'s own table
markup (a literal ``<td>`` string, not five drawn bitmaps) -- unlike
``main_frame.py``'s Card column or ``entry_detail.py``'s cards_list/
laps_list, ``standings_list`` needs no ``DataViewBitmapRenderer``.
:func:`format_best5` is the pure text formatter this column uses, and
:func:`format_place` the E6.4.1 ⚠ badge formatter (a draw_required row
renders ``"⚠ 2"`` in its Place cell -- this task's own reading of the
footnote's "⚠ badge column": the canvas pins exactly seven columns
and shows no tie rows, so a new eighth column would shift every
frozen column index; the badge instead leads the Place cell, where a
scorer's eye lands first).

E6.4.1 (P9) completes the E1.5.2 scope: ``show_publish_options`` sets
the five publish checkboxes from an ``ExportOptions``; ``set_stale``
shows/hides the code-side ``stale_infobar`` (xrc-windows.md's
code-side footnote; XRC cannot author a ``wxInfoBar`` -- results.xrc's
own header); ``show_times_chk`` also toggles the Total column;
``tiebreak_list`` is seeded from the ride's stored ``tiebreak_order``
via the presenter's plain-label map, and its NATIVE up/down reorder
buttons (``GetUpButton()``/``GetDownButton()`` -- no custom buttons
are added) notify the presenter to re-read ``GetStrings()`` and
re-rank live. The window's one presenter (``self.presenter``, built
here like ``RideSetup`` builds its own) owns that label map and the
``ExportOptions`` the export handlers (E6.4.2) read.

``_find`` is now shared via ``ui.views._support.find_control`` --
see that module's docstring for why it used to be duplicated here.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.adv
import wx.dataview

from rivercrossing.htmlexport import ExportOptions
from rivercrossing.ride import DEFAULT_TIEBREAK_ORDER
from rivercrossing.ui import ids
from rivercrossing.ui.presenters.results import ResultsPresenter
from rivercrossing.ui.views._support import associate_model, find_control

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.ui.presenters.data_source import DataSource, StandingsRow

__all__ = [
    "COLUMN_LABELS",
    "COL_BEST5",
    "COL_ENTRY",
    "COL_HAND",
    "COL_LAPS",
    "COL_PLACE",
    "COL_PLATE",
    "COL_TOTAL",
    "JOKER_DISPLAY",
    "MIN_SIZE",
    "STALE_INFOBAR",
    "TIE_BADGE",
    "ResultsWindow",
    "StandingsListModel",
    "format_best5",
    "format_card",
    "format_place",
]

COL_PLACE = 0
COL_PLATE = 1
COL_ENTRY = 2
COL_LAPS = 3
COL_TOTAL = 4
COL_BEST5 = 5
COL_HAND = 6

# xrc-windows.md D's exact column order.
COLUMN_LABELS: tuple[str, ...] = ("Place", "Plate", "Entry", "Laps", "Total", "Best 5", "Hand")

_SUIT_SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
JOKER_CODE = "JK"
JOKER_DISPLAY = "JK★"

# E6.4.1: the R-43 "draw required" badge (xrc-windows.md D's code-side
# footnote), rendered as a leading glyph in the Place cell (module
# docstring).
TIE_BADGE = "⚠"

# D16: the canvas draws this window at 720px; XRC has no window-level
# minsize (results.xrc's own header notes this and defers to code).
# Height is Fit()'s own measurement of the real sizer content -- see
# this task's own report for how it was measured.
MIN_SIZE = (720, 442)

# The stale-export InfoBar's frozen name (xrc-windows.md D / spec.md
# §15b). XRC cannot author a wxInfoBar at all (results.xrc's own
# header), so this name never appears in ui/ids.py -- the bar is built
# code-side and named with SetName(), mirroring main_frame.py's
# RESUME_INFOBAR/REOPENED_INFOBAR/FINISHED_INFOBAR precedent.
STALE_INFOBAR = "stale_infobar"


# E6.4.2: the results-frame export buttons and the menu rows they fire,
# so one handler implementation serves both surfaces.
_EXPORT_BUTTONS: tuple[tuple[str, str], ...] = (
    ("export_html_btn", ids.MI_EXPORT_HTML),
    ("export_pdf_btn", ids.MI_EXPORT_PDF),
    ("poster_btn", ids.MI_EXPORT_POSTER),
    ("export_csv_btn", ids.MI_EXPORT_RESULTS_CSV),
)


def format_card(code: str) -> str:
    """Return one stored card code's canvas display text.

    ``"KS"`` -> ``"K♠"``; the joker -> ``"JK★"``. The rank character
    is already in its display form (``Card.code()``'s stored form
    uses "T" for ten, module-skeletons.md S4), so only the suit
    letter needs converting to a glyph.
    """
    if code == JOKER_CODE:
        return JOKER_DISPLAY
    rank, suit = code[:-1], code[-1]
    return f"{rank}{_SUIT_SYMBOLS[suit]}"


def format_best5(cards: Sequence[str]) -> str:
    """Return ``standings_list``'s "Best 5" cell text for *cards*.

    Space-joined, canvas exact: ``("KS", "KC", "KD", "JK", "9H")`` ->
    ``"K♠ K♣ K♦ JK★ 9♥"``.
    """
    return " ".join(format_card(card) for card in cards)


def format_place(standing: StandingsRow) -> str:
    """Return ``standings_list``'s Place cell text for *standing*.

    A ``draw_required`` row (R-43's unresolved hand tie) carries the
    ⚠ badge ahead of its place -- ``"⚠ 2"`` -- the E6.4.1 reading of
    the footnote's "⚠ badge column" (module docstring); every other
    row is the bare place number.
    """
    if standing.draw_required:
        return f"{TIE_BADGE} {standing.place}"
    return str(standing.place)


_TEXT_ACCESSORS: tuple[Callable[[StandingsRow], str], ...] = (
    format_place,
    lambda standing: standing.plate,
    lambda standing: standing.entry,
    lambda standing: str(standing.laps),
    lambda standing: standing.total,
    lambda standing: format_best5(standing.best5),
    lambda standing: standing.hand,
)


class StandingsListModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over ``StandingsRow`` rows, standings_list.

    ``# type: ignore[misc]``: wx ships no stubs, so mypy refuses to
    subclass ``Any`` -- the same unavoidable annotation
    ``CrossingsFeedModel`` carries in ``views/main_frame.py``.
    """

    def __init__(self, rows: Sequence[StandingsRow]) -> None:
        """Wrap *rows*, in placed order."""
        super().__init__(len(rows))
        self._rows = tuple(rows)

    def GetColumnCount(self) -> int:
        """Return the standings' fixed seven columns."""
        return len(COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:  # noqa: ARG002 -- every column is text here
        """Return "string" -- every column here is text."""
        return "string"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the cell value at *row*/*col*."""
        return _TEXT_ACCESSORS[col](self._rows[row])


class ResultsWindow:
    """Code-side behaviour for ``results_frame`` (1f).

    Implements the :class:`~rivercrossing.ui.presenters.results.
    ResultsView` contract in full (E6.4.1): ``show_standings``,
    ``set_stale`` (the code-side stale_export banner, hidden until
    E7.3.2 triggers it), ``show_publish_options``/``publish_options``
    (the five publish checkboxes), and the tie-break seed/restore
    channel (``set_tiebreak_labels``) plus a status notice. The one
    live presenter is built here, the same ``RideSetup`` precedent.
    """

    def __init__(
        self,
        frame: wx.Frame,
        *,
        data_source: DataSource,
        tiebreak_order: tuple[str, str, str] = DEFAULT_TIEBREAK_ORDER,
    ) -> None:
        """Decorate an already-loaded ``results_frame`` window.

        Args:
            frame: The ``wx.Frame`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``results.xrc``.
            data_source: The display-data seam. This view knows only
                the :class:`~rivercrossing.ui.presenters.data_source.
                DataSource` Protocol -- the caller wires in whichever
                implementation applies.
            tiebreak_order: The ride's stored tie-break spellings, in
                priority order; the presenter seeds ``tiebreak_list``
                from them (``RideConfig.tiebreak_order``).
        """
        self.frame = frame
        self.data_source = data_source

        self.standings_list = self._find(ids.STANDINGS_LIST, wx.dataview.DataViewCtrl)
        self.show_times_chk = self._find(ids.SHOW_TIMES_CHK, wx.CheckBox)
        self.laps_board_chk = self._find(ids.LAPS_BOARD_CHK, wx.CheckBox)
        self.time_board_chk = self._find(ids.TIME_BOARD_CHK, wx.CheckBox)
        self.full_field_chk = self._find(ids.FULL_FIELD_CHK, wx.CheckBox)
        self.all_cards_chk = self._find(ids.ALL_CARDS_CHK, wx.CheckBox)
        self.tiebreak_list = self._find(ids.TIEBREAK_LIST, wx.adv.EditableListBox)

        self._total_column = self._build_columns()
        self._apply_show_times_column()
        self._model: StandingsListModel | None = None

        self.stale_infobar = self._build_infobar()

        self.presenter = ResultsPresenter(self, data_source, tiebreak_order=tiebreak_order)

        self._bind_events()
        self._bind_export_buttons()
        self._apply_min_size()

    def _find(self, name: str, expected_type: type = wx.Window) -> Any:  # noqa: ANN401
        """Resolve one of this frame's own child controls by name.

        See :func:`find_control`'s docstring (``ui.views._support``)
        for the full measured reasoning this mirrors.

        Raises:
            LookupError: If *name* does not resolve to an
                *expected_type* instance inside this frame, even
                after settling.
        """
        return find_control(self.frame, name, expected_type)

    def _bind_export_buttons(self) -> None:
        """Route the four export buttons through the menu command table.

        E6.4.2: each button fires the same ``mi_export_*`` event the
        Results menu row does, so one handler implementation serves
        both surfaces -- the frame's bound ``EVT_MENU`` handlers run
        the export off-loop (R-02).
        """
        import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

        for button_name, menu_id in _EXPORT_BUTTONS:
            button = self._find(button_name, wx.Button)
            button.Bind(
                wx.EVT_BUTTON,
                lambda _event, mid=menu_id: self.frame.GetEventHandler().ProcessEvent(
                    wx.CommandEvent(wx.EVT_MENU.typeId, wx.xrc.XRCID(mid))
                ),
            )

    def _build_columns(self) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Append ``standings_list``'s seven columns in canvas order.

        Returns:
            The Total column (``COL_TOTAL``), the one
            ``show_times_chk`` toggles hidden (results.xrc's own
            code-side footnote: "hides Total col here too").
        """
        total: Any = None
        for col, label in enumerate(COLUMN_LABELS):
            column = self.standings_list.AppendTextColumn(label, col)
            if col == COL_TOTAL:
                total = column
        return total

    def _build_infobar(self) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Build the code-side :data:`STALE_INFOBAR`, inserted on top.

        Mirrors ``main_frame.MainFrame._build_infobar``'s measured
        slide-effect hang fix: both effects disabled, so a later
        ``ShowMessage()``/``Dismiss()`` (E7.3.2's trigger) returns.
        results.xrc reserves sizer index 0 (a zero-size spacer) for
        this bar; inserting at index 0 displaces the spacer.
        """
        bar = wx.InfoBar(self.frame)
        bar.SetName(STALE_INFOBAR)
        bar.SetShowHideEffects(wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)
        self.frame.GetSizer().Insert(0, bar, 0, wx.EXPAND)
        return bar

    def _bind_events(self) -> None:
        """Forward every control event straight to the presenter."""
        for checkbox in (
            self.show_times_chk,
            self.laps_board_chk,
            self.time_board_chk,
            self.full_field_chk,
            self.all_cards_chk,
        ):
            self.frame.Bind(wx.EVT_CHECKBOX, self._on_publish_toggle, checkbox)
        # The EditableListBox's NATIVE up/down reorder buttons (no
        # custom buttons added). Binding on the buttons and calling
        # Skip lets the native reorder run during this same dispatch;
        # the re-read is deferred through wx.CallAfter so GetStrings()
        # reflects the post-reorder rows, not the pre-click ones.
        self.tiebreak_list.GetUpButton().Bind(wx.EVT_BUTTON, self._on_tiebreak_clicked)
        self.tiebreak_list.GetDownButton().Bind(wx.EVT_BUTTON, self._on_tiebreak_clicked)

    def _on_publish_toggle(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle a publish-checkbox click; forward it to the presenter.

        ``show_times_chk`` also toggles the Total column (results.xrc's
        own footnote) -- a structural sibling-control fact the view
        owns, the same ``RideSetup._on_cap_toggle`` precedent.
        """
        event.Skip()
        if event.GetEventObject() is self.show_times_chk:
            self._apply_show_times_column()
        self.presenter.on_publish_toggled()

    def _on_tiebreak_clicked(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle a native Up/Down click: re-read rows after reorder."""
        event.Skip()
        wx.CallAfter(self._notify_tiebreak_reordered)

    def _notify_tiebreak_reordered(self) -> None:
        """Hand the control's post-reorder rows to the presenter."""
        self.presenter.on_tiebreak_reordered(list(self.tiebreak_list.GetStrings()))

    def _apply_show_times_column(self) -> None:
        """Hide the Total column unless show_times_chk is checked."""
        self._total_column.SetHidden(not self.show_times_chk.GetValue())

    def show_standings(self, rows: list[StandingsRow]) -> None:
        """Render ``standings_list`` (``ResultsView``).

        See ``ui.views._support.associate_model``'s docstring for
        why this repaints explicitly (unverified remedy).
        """
        self._model = StandingsListModel(rows)
        associate_model(self.standings_list, self._model)

    def set_stale(self, *, stale: bool) -> None:
        """Show/hide :data:`STALE_INFOBAR` (``ResultsView``, E6.4.1).

        Hidden by default; E7.3.2 shows it after reopened corrections
        and clears it on re-export. ``wx.InfoBar`` starts hidden
        (measured), so constructing the bar is all the "hidden"
        state needs.
        """
        if stale:
            self.stale_infobar.ShowMessage(
                "Results are stale — re-export to refresh", wx.ICON_WARNING
            )
        else:
            self.stale_infobar.Dismiss()
        self.frame.Layout()

    def show_publish_options(self, options: ExportOptions) -> None:
        """Reflect the five publish checkboxes (``ResultsView``).

        ``SetValue`` fires no ``EVT_CHECKBOX`` (measured harness
        convention), so this cannot loop back into the presenter.
        """
        self.show_times_chk.SetValue(options.show_times)
        self.laps_board_chk.SetValue(options.laps_board)
        self.time_board_chk.SetValue(options.time_board)
        self.full_field_chk.SetValue(options.full_field)
        self.all_cards_chk.SetValue(options.all_cards)
        self._apply_show_times_column()

    def set_tiebreak_labels(self, labels: list[str]) -> None:
        """Seed ``tiebreak_list``'s rows (``ResultsView``, E6.4.1).

        Called by the presenter at construction (the ride's stored
        order, as plain labels) and on an unrecognised reorder (the
        last-known-good order).
        """
        self.tiebreak_list.SetStrings(labels)

    def show_notice(self, text: str) -> None:
        """Show a transient status notice (``ResultsView``, E6.4.1).

        The results frame declares no status bar in results.xrc, so
        ``wx.Frame.SetStatusText`` is a silent no-op today -- the
        notice channel exists for the presenter's unrecognised-reorder
        path (the same New/Delete gap ride_setup.py documents); a
        follow-up that adds a status bar to the window makes it
        visible without changing this method.
        """
        self.frame.SetStatusText(text)

    def publish_options(self) -> ExportOptions:
        """Return the five publish checkboxes as ``ExportOptions``.

        ``lap_km`` stays at its dataclass default -- the results
        window has no course-length control; E6.4.2's export handlers
        own that render-only setting.
        """
        return ExportOptions(
            show_times=self.show_times_chk.GetValue(),
            laps_board=self.laps_board_chk.GetValue(),
            time_board=self.time_board_chk.GetValue(),
            full_field=self.full_field_chk.GetValue(),
            all_cards=self.all_cards_chk.GetValue(),
        )

    def _apply_min_size(self) -> None:
        """Force the canvas's 720px floor, then Fit() the rest (D16).

        See :meth:`ride_library.RideLibrary._apply_min_size`'s
        docstring for the measured ``SetMinSize`` + ``Fit()``
        reasoning this mirrors.
        """
        self.frame.SetMinSize(wx.Size(MIN_SIZE[0], -1))
        self.frame.Fit()
