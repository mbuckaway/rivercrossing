# SPDX-License-Identifier: GPL-3.0-only
"""``ResultsWindow``: results_dlg (1f), standings (E1.5.2/E6.4.1).

xrc-windows.md section D's code-side footnote puts the standings
lists' columns and rows in code -- ``results.xrc``'s own header
explains why (``wxDataViewListCtrl`` would overwrite the frozen
name). This module is that binding.

The canvas's "Best 5" cell is plain text carrying suit glyphs ("K♠
K♣ K♦ JK★ 9♥"), confirmed against ``design/docs-html``'s own table
markup (a literal ``<td>`` string, not five drawn bitmaps) -- unlike
``main_frame.py``'s Card column or ``entry_detail.py``'s cards_list/
laps_list, the standings lists need no ``DataViewBitmapRenderer``.
:func:`format_best5` is the pure text formatter this column uses, and
:func:`format_place` the E6.4.1 ⚠ badge formatter (a draw_required row
renders ``"⚠ 2"`` in its Place cell -- this task's own reading of the
footnote's "⚠ badge column": the canvas's own columns and their order
are frozen, so a badge column would shift every frozen index; the
badge instead leads the Place cell, where a scorer's eye lands first,
and Phase 5's activation alert explains it).

Phase 3 split the standings by entry kind. A MIXED ride renders its
Teams and Solo sections on the two ``results_notebook`` pages
(``teams_standings_list``/``solo_standings_list``); a SOLO-only ride
hides the notebook and shows the one standalone ``standings_list``.
The view switches on the ``entry_mode`` the app threads in, so the
kind split needs no extra column and no merged header row.

E7.3.2's stale-export flag is the one live banner: ``set_stale``
shows/hides the code-side ``stale_infobar`` (xrc-windows.md's
code-side footnote; XRC cannot author a ``wxInfoBar`` -- results.xrc's
own header). ``show_times_chk`` also toggles the two time columns --
Total and Best lap -- on every list here and gates the Fastest-time
box: with times off the box is cleared and disabled, so the
time_board/time-off combination cannot be requested (R-63). The
window's one presenter (``self.presenter``, built here like
``RideSetup`` builds its own) holds the ``ExportOptions`` the export
handlers (E6.4.2) read.

Phase 5 adds three display facts the canvas cannot carry:

- the ⚠ badge's explanation (Part 1). A ``wxDataViewCtrl`` has no
  per-row hover tooltip, and hover is unreachable by keyboard anyway
  (CODINGSTANDARDS-UX-DESKTOP §7), so the badge is explained on the
  activation gesture instead: double-clicking (or pressing Enter on) a
  draw row opens an OK-only alert carrying the row's own
  ``tie_note``. ``standings``' draw flag and note are threaded through
  ``StandingsRow`` for exactly this.
- the Plate column's absence on the Team list under ``RIDER_POOLED``
  (Part 2): a pooled team's plate is *derived* from its members, so
  the column repeats a member's number while the Entry column names
  the team. Under ``TEAM_RELAY`` the plate is the entry's identity and
  stays.
- the Best lap column (Part 3), positioned after Total and gated by
  ``show_times_chk`` together with it -- both render time data.

W11 wires the four export buttons: the app threads an
``on_export(target)`` callback (its own ``_handle_export_command``
route, the same one each ``mi_export_*`` menu row runs) into the
dialog at decoration time. This replaces the dead synthetic-event
mechanism -- forwarding a synthetic ``EVT_MENU`` through the results
window's own handler chain never reached the main frame where the
``mi_export_*`` handlers are bound. The buttons are enabled only for
a FINISHED ride, the same state gate the export menu rows carry. One
handler implementation serves the menu row and the button.

``_find`` is now shared via ``ui.views._support.find_control`` --
see that module's docstring for why it used to be duplicated here.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.dataview

from rivercrossing.htmlexport import ExportOptions
from rivercrossing.ride import DEFAULT_TIEBREAK_ORDER, RideStatus
from rivercrossing.roster import EntryMode, PlateModel
from rivercrossing.standings import DRAW_TIE_NOTE
from rivercrossing.ui import ids
from rivercrossing.ui.card_text import JOKER_CODE, JOKER_DISPLAY, format_card
from rivercrossing.ui.presenters.results import ResultsPresenter
from rivercrossing.ui.std_dialogs import show_info
from rivercrossing.ui.views._support import _ordering, associate_model, find_control

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.ui.presenters.data_source import DataSource, StandingsRow

__all__ = [
    "COLUMN_LABELS",
    "COL_BEST5",
    "COL_BESTLAP",
    "COL_ENTRY",
    "COL_HAND",
    "COL_LAPS",
    "COL_PLACE",
    "COL_PLATE",
    "COL_TOTAL",
    "DRAW_EXPLANATION",
    "DRAW_INFO_TITLE",
    "JOKER_CODE",
    "JOKER_DISPLAY",
    "MIN_SIZE",
    "STALE_INFOBAR",
    "STANDINGS_COLUMN_FLAGS",
    "TIE_BADGE",
    "TIME_COLUMNS",
    "ResultsWindow",
    "StandingsListModel",
    "draw_info_message",
    "format_best5",
    "format_card",
    "format_place",
]

COL_PLACE = 0
COL_PLATE = 1
COL_ENTRY = 2
COL_LAPS = 3
COL_TOTAL = 4
COL_BESTLAP = 5
COL_BEST5 = 6
COL_HAND = 7

# xrc-windows.md D's column order, with the Phase 5 Best lap column
# placed directly after Total: both show the same ride clock, and only
# show_times_chk hides them (results.xrc's own code-side footnote).
COLUMN_LABELS: tuple[str, ...] = (
    "Place",
    "Plate",
    "Entry",
    "Laps",
    "Total",
    "Best lap",
    "Best 5",
    "Hand",
)

# The columns show_times_chk governs -- the two that render time data.
TIME_COLUMNS: tuple[int, ...] = (COL_TOTAL, COL_BESTLAP)

# E6.4.1: the R-43 "draw required" badge (xrc-windows.md D's code-side
# footnote), rendered as a leading glyph in the Place cell (module
# docstring).
TIE_BADGE = "⚠"

# The ⚠ badge's own explanation (Phase 5, Part 1). A wxDataViewCtrl has
# no per-row hover tooltip and hover is keyboard-unreachable anyway
# (CODINGSTANDARDS-UX-DESKTOP §7), so the badge is explained on the
# activation gesture -- double-click or Enter -- in an OK-only alert:
# the row's own tie note (standings' DRAW_TIE_NOTE), then this one plain
# sentence saying what the flag means and who decides.
DRAW_INFO_TITLE = "Draw required"
DRAW_EXPLANATION = (
    "Identical best hands were not resolved by the tie-break — the venue draw arbitrates."
)

# D16: XRC has no window-level minsize (results.xrc's own header notes
# this and defers to code). The width floor is measured on wxPython
# 4.3.1 / wxWidgets 3.3.3: the publish-checkbox row's static box needs
# 735px and the export-button+Close row 539px, so 735 + the two 10px
# sizeritem borders = 755. At the old 720 the five checkboxes wrapped
# onto a second row. Height is Fit()'s own measurement of the real
# sizer content -- see this task's own report for how it was measured.
MIN_SIZE = (755, 442)

# The stale-export InfoBar's frozen name (xrc-windows.md D / spec.md
# §15b). XRC cannot author a wxInfoBar at all (results.xrc's own
# header), so this name never appears in ui/ids.py -- the bar is built
# code-side and named with SetName(), mirroring main_frame.py's
# RESUME_INFOBAR/REOPENED_INFOBAR/FINISHED_INFOBAR precedent.
STALE_INFOBAR = "stale_infobar"

# AppendTextColumn's own default flags include
# wxDATAVIEW_COL_RESIZABLE, but an explicit flags= argument *replaces*
# the default rather than OR-ing into it -- macOS then sets the column
# NSTableColumnNoResizing -- so both bits must be spelled out
# (mirrors rider_editor.py's RIDERS_LIST_COLUMN_FLAGS).
STANDINGS_COLUMN_FLAGS = wx.dataview.DATAVIEW_COL_SORTABLE | wx.dataview.DATAVIEW_COL_RESIZABLE


# E6.4.2: the export buttons and the route targets they fire, so one
# handler implementation serves both surfaces (W11: the values are the
# ``_handle_export_command`` route targets, matching the Results menu
# rows' dispatch).
_EXPORT_BUTTONS: tuple[tuple[str, str], ...] = (
    ("export_html_btn", "export_html"),
    ("export_pdf_btn", "export_pdf"),
    ("poster_btn", "export_poster"),
    ("export_csv_btn", "export_results_csv"),
)


def format_best5(cards: Sequence[str]) -> str:
    """Return the "Best 5" cell text for *cards*.

    Space-joined, canvas exact: ``("KS", "KC", "KD", "JK", "9H")`` ->
    ``"K♠ K♣ K♦ JK★ 9♥"``.
    """
    return " ".join(format_card(card) for card in cards)


def format_place(standing: StandingsRow) -> str:
    """Return the Place cell text for *standing*.

    A ``draw_required`` row (R-43's unresolved hand tie) carries the
    ⚠ badge ahead of its place -- ``"⚠ 2"`` -- the E6.4.1 reading of
    the footnote's "⚠ badge column" (module docstring); every other
    row is the bare place number.
    """
    if standing.draw_required:
        return f"{TIE_BADGE} {standing.place}"
    return str(standing.place)


def draw_info_message(standing: StandingsRow) -> str:
    """Return the ⚠ explanation alert's body for *standing*.

    The row's own tie note leads (``standings.DRAW_TIE_NOTE``, "draw
    required"), then :data:`DRAW_EXPLANATION`'s plain sentence. A row
    whose note is unset -- a hand-built row, or an export-era stub --
    still reads "draw required" rather than the word "None".
    """
    note = standing.tie_note or DRAW_TIE_NOTE
    return f"{note}\n\n{DRAW_EXPLANATION}"


_TEXT_ACCESSORS: tuple[Callable[[StandingsRow], str], ...] = (
    format_place,
    lambda standing: standing.plate,
    lambda standing: standing.entry,
    lambda standing: str(standing.laps),
    lambda standing: standing.total,
    lambda standing: standing.best_lap,
    lambda standing: format_best5(standing.best5),
    lambda standing: standing.hand,
)

# The native header sort's per-column key, in ``COLUMN_LABELS`` order:
# Place/Laps are ints, Total and Best lap sort on the stored numeric
# seconds (never their rendered ``h:mm:ss`` text), and the rest are
# strings.
_STANDINGS_SORT_KEYS: tuple[Callable[[StandingsRow], Any], ...] = (
    lambda standing: standing.place,
    lambda standing: standing.plate,
    lambda standing: standing.entry,
    lambda standing: standing.laps,
    lambda standing: standing.total_seconds,
    lambda standing: standing.best_lap_seconds,
    lambda standing: format_best5(standing.best5),
    lambda standing: standing.hand,
)


class StandingsListModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over ``StandingsRow`` rows for a standings list.

    ``# type: ignore[misc]``: wx ships no stubs, so mypy refuses to
    subclass ``Any`` -- the same unavoidable annotation
    ``CrossingsFeedModel`` carries in ``views/main_frame.py``.
    """

    def __init__(self, rows: Sequence[StandingsRow]) -> None:
        """Wrap *rows*."""
        super().__init__(len(rows))
        self._rows = tuple(rows)

    def GetColumnCount(self) -> int:
        """Return the standings' fixed eight columns."""
        return len(COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:  # noqa: ARG002 -- every column is text here
        """Return "string" -- every column here is text."""
        return "string"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the cell value at *row*/*col*."""
        return _TEXT_ACCESSORS[col](self._rows[row])

    def standing_at(self, row: int) -> StandingsRow | None:
        """Return the row at *row*, or None when *row* names none.

        The activation handler's own lookup: ``GetRow`` answers a
        not-found item with wxNOT_FOUND (0xFFFFFFFF, measured on this
        build), so the range guard is what turns every unresolvable item
        into "no row" rather than a crash.
        """
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

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
        rows those items index (``DataViewIndexListModel.GetRow``),
        keyed per column by :data:`_STANDINGS_SORT_KEYS`. Place and
        Laps compare as integers and Total as numeric seconds, so a
        displayed ``"10:00:00"`` never sorts before ``"9:00:00"``.

        Equal keys fall back to the row's own position, which is
        unique: wx's control-side sort is not stable, so without the
        tie-break two rows showing the same cell could reorder freely
        between sorts. The tie-break is deliberately *not* negated for
        the downward arrow, so equal-key rows keep their original
        order in both directions (mirrors
        ``RiderRowListModel.Compare`` in ``ui.views._support``).
        *ascending* is the arrow's own direction.
        """
        first_row = self.GetRow(item1)
        second_row = self.GetRow(item2)
        sort_key = _STANDINGS_SORT_KEYS[col]
        result = _ordering(sort_key(self._rows[first_row]), sort_key(self._rows[second_row]))
        if result == 0:
            return _ordering(first_row, second_row)
        return result if ascending else -result


class ResultsWindow:
    """Code-side behaviour for ``results_dlg`` (1f).

    Implements the :class:`~rivercrossing.ui.presenters.results.
    ResultsView` contract in full (E6.4.1): ``show_standings`` (the
    MIXED notebook or the SOLO standalone list, per ``entry_mode``),
    ``set_stale`` (the code-side stale_export banner E7.3.2 triggers
    after post-export corrections), and ``show_publish_options``/
    ``publish_options`` (the five publish checkboxes). The one live
    presenter is built here, the same ``RideSetup`` precedent.
    """

    def __init__(  # noqa: PLR0913 -- (dialog, data_source) + the tie-break order, export-watermark, entry-mode, plate-model and export seams
        self,
        dialog: wx.Dialog,
        *,
        data_source: DataSource,
        tiebreak_order: tuple[str, str, str] = DEFAULT_TIEBREAK_ORDER,
        export_watermark: int | None = None,
        entry_mode: EntryMode = EntryMode.SOLO,
        plate_model: PlateModel = PlateModel.RIDER_POOLED,
        on_export: Callable[[str], None] | None = None,
    ) -> None:
        """Decorate an already-loaded ``results_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` the caller already loaded
                from ``results.xrc``.
            data_source: The display-data seam. This view knows only
                the :class:`~rivercrossing.ui.presenters.data_source.
                DataSource` Protocol -- the caller wires in whichever
                implementation applies.
            tiebreak_order: The ride's stored tie-break spellings, in
                priority order (``RideConfig.tiebreak_order``); the
                presenter ranks the standings with them.
            export_watermark: The engine event count at the last
                export (E7.3.2); the presenter evaluates the stale
                banner against it on the first render. ``None`` when
                nothing was exported.
            entry_mode: The ride's entry mode (``RideConfig.
                entry_mode``). MIXED shows the two-page notebook; SOLO
                shows the standalone ``standings_list``.
            plate_model: The ride's plate policy (``RideConfig.
                plate_model``). Under ``RIDER_POOLED`` the Team list
                drops its Plate column: a pooled team's plate is derived
                from its members' plates, so the column repeats one
                member's number and the Entry column is the team's
                identity. Under ``TEAM_RELAY`` the entry's own plate is
                the identity, so the column stays.
            on_export: The app's export flow (W11) -- the same
                ``_handle_export_command`` route each ``mi_export_*``
                menu row runs. Each export button fires it with the
                button's route target; ``None`` leaves the buttons
                inert (a results window with no live ride).
        """
        self.dialog = dialog
        self.data_source = data_source
        self.entry_mode = entry_mode
        self.plate_model = plate_model
        self.on_export = on_export

        self.standings_list = self._find(ids.STANDINGS_LIST, wx.dataview.DataViewCtrl)
        self.teams_standings_list = self._find(ids.TEAMS_STANDINGS_LIST, wx.dataview.DataViewCtrl)
        self.solo_standings_list = self._find(ids.SOLO_STANDINGS_LIST, wx.dataview.DataViewCtrl)
        self.results_notebook = self._find(ids.RESULTS_NOTEBOOK, wx.Notebook)
        self.show_times_chk = self._find(ids.SHOW_TIMES_CHK, wx.CheckBox)
        self.laps_board_chk = self._find(ids.LAPS_BOARD_CHK, wx.CheckBox)
        self.time_board_chk = self._find(ids.TIME_BOARD_CHK, wx.CheckBox)
        self.full_field_chk = self._find(ids.FULL_FIELD_CHK, wx.CheckBox)
        self.all_cards_chk = self._find(ids.ALL_CARDS_CHK, wx.CheckBox)

        self._time_columns = self._build_columns()
        self._apply_show_times_state()
        self._model: StandingsListModel | None = None
        self._teams_model: StandingsListModel | None = None
        self._solo_model: StandingsListModel | None = None

        self.stale_infobar = self._build_infobar()

        self.presenter = ResultsPresenter(
            self,
            data_source,
            tiebreak_order=tiebreak_order,
            export_watermark=export_watermark,
        )
        # E7.3.2: app.py's export completion (and E6.4.2's own
        # ``_export_options``) finds the open window's presenter through
        # ``wx.FindWindowByName(RESULTS_DLG).presenter`` -- the E6.4.2
        # contract this view was always meant to fulfil. wxPython
        # wrapper objects hold instance attributes (the ``_spy_repaint``
        # precedent in _lists_common.py); the wrapper stays alive while
        # the window's event bindings hold this view, and a closed
        # window's lookup returns None, so the seam self-clears.
        self.dialog.presenter = self.presenter

        self._bind_events()
        self._bind_export_buttons()
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

    def _bind_export_buttons(self) -> None:
        """Wire the four export buttons to the app's export flow (W11).

        ``on_export`` is the app's own ``_handle_export_command``
        (the flow each ``mi_export_*`` menu row runs), threaded at
        decoration time. Each button fires it with its route target.
        A results window with no live ride (``None``) leaves the
        buttons inert. Every button is enabled only while the ride is
        FINISHED, matching the export menu rows' own state gate (Part
        D: exports are gated on FINISHED).
        """
        finished = self.data_source.ride_status() is RideStatus.FINISHED
        on_export = self.on_export
        for button_name, target in _EXPORT_BUTTONS:
            button = self._find(button_name, wx.Button)
            button.Enable(finished)
            if on_export is not None:
                button.Bind(wx.EVT_BUTTON, lambda _event, t=target: on_export(t))

    def _build_columns(self) -> tuple[Any, ...]:
        """Build every standings list's columns.

        The Team list drops its Plate column under ``RIDER_POOLED``
        (Part 2): a pooled team's plate is derived from its members, so
        that column repeats a member's plate rather than naming the
        entry, and the Entry column already carries the team's name.
        The standalone and Solo lists always keep theirs, as does the
        Team list under ``TEAM_RELAY``.

        Returns:
            The Total and Best lap columns (``TIME_COLUMNS``) of each
            list, in (standalone, notebook Teams, notebook Solo) order
            -- the columns ``show_times_chk`` toggles hidden
            (results.xrc's own code-side footnote: "hides Total col here
            too").
        """
        hide_team_plate = self.plate_model is PlateModel.RIDER_POOLED
        return tuple(
            column
            for control, hide_plate in (
                (self.standings_list, False),
                (self.teams_standings_list, hide_team_plate),
                (self.solo_standings_list, False),
            )
            for column in self._build_columns_for(control, hide_plate=hide_plate)
        )

    @staticmethod
    def _build_columns_for(control: Any, *, hide_plate: bool = False) -> tuple[Any, ...]:  # noqa: ANN401 -- wx ships no stubs
        """Append one list's eight columns in canvas order.

        Args:
            control: The ``DataViewCtrl`` to append to.
            hide_plate: Hide the Plate column (a pooled team's list).

        Returns:
            *control*'s time columns (:data:`TIME_COLUMNS`) -- Total,
            then Best lap.
        """
        columns = [
            control.AppendTextColumn(label, col, flags=STANDINGS_COLUMN_FLAGS)
            for col, label in enumerate(COLUMN_LABELS)
        ]
        if hide_plate:
            columns[COL_PLATE].SetHidden(True)  # noqa: FBT003 -- wx API takes a positional bool
        return tuple(columns[col] for col in TIME_COLUMNS)

    def _build_infobar(self) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Build the code-side :data:`STALE_INFOBAR`, inserted on top.

        Mirrors ``main_frame.MainFrame._build_infobar``'s measured
        slide-effect hang fix: both effects disabled, so a later
        ``ShowMessage()``/``Dismiss()`` (E7.3.2's trigger) returns.
        results.xrc reserves sizer index 0 (a zero-size spacer) for
        this bar; inserting at index 0 displaces the spacer.
        """
        bar = wx.InfoBar(self.dialog)
        bar.SetName(STALE_INFOBAR)
        bar.SetShowHideEffects(wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)
        self.dialog.GetSizer().Insert(0, bar, 0, wx.EXPAND)
        return bar

    def _bind_events(self) -> None:
        """Forward the publish checkboxes and the list activations.

        Every publish checkbox forwards to the presenter; each standings
        list explains its own ⚠ rows on the activation gesture
        (Part 1).
        """
        for checkbox in (
            self.show_times_chk,
            self.laps_board_chk,
            self.time_board_chk,
            self.full_field_chk,
            self.all_cards_chk,
        ):
            self.dialog.Bind(wx.EVT_CHECKBOX, self._on_publish_toggle, checkbox)
        for control in (
            self.standings_list,
            self.teams_standings_list,
            self.solo_standings_list,
        ):
            control.Bind(wx.dataview.EVT_DATAVIEW_ITEM_ACTIVATED, self._on_standings_activated)

    def _on_publish_toggle(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle a publish-checkbox click; forward it to the presenter.

        ``show_times_chk`` also toggles the two time columns and gates
        the Fastest-time board (results.xrc's own footnote) -- a
        structural sibling-control fact the view owns, the same
        ``RideSetup._on_cap_toggle`` precedent.
        """
        event.Skip()
        if event.GetEventObject() is self.show_times_chk:
            self._apply_show_times_state()
        self.presenter.on_publish_toggled()

    def _on_standings_activated(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Explain the activated row's ⚠ badge (Part 1).

        Double-clicking (or pressing Enter on) a ``draw_required`` row
        opens the OK-only information alert carrying the row's own tie
        note and :data:`DRAW_EXPLANATION`; any other row does nothing.
        The row resolves through the model the event was raised with --
        the same model the list is rendering -- so a list sharing this
        handler cannot explain another list's row.
        """
        model = event.GetModel()
        if not isinstance(model, StandingsListModel):
            return
        standing = model.standing_at(model.GetRow(event.GetItem()))
        if standing is None or not standing.draw_required:
            return
        show_info(self.dialog, DRAW_INFO_TITLE, draw_info_message(standing))

    def _apply_show_times_state(self) -> None:
        """Apply the sibling controls ``show_times_chk`` governs.

        Times off hides both time columns (Total and Best lap) on every
        list and makes the Fastest-time board unrequestable: its box is
        cleared and disabled, so ``publish_options()`` can never map a
        time board while no times are shown (R-63 -- the board is
        nothing but time data). Re-checking show_times re-enables the
        box.
        """
        show_times = self.show_times_chk.GetValue()
        for column in self._time_columns:
            column.SetHidden(not show_times)
        if not show_times:
            self.time_board_chk.SetValue(False)  # noqa: FBT003 -- wx API takes a positional bool
        self.time_board_chk.Enable(show_times)

    def show_standings(self, teams: list[StandingsRow], solo: list[StandingsRow]) -> None:
        """Render the standings (``ResultsView``, Phase 3).

        A MIXED ride associates a flat model over *teams* with
        ``teams_standings_list`` and one over *solo* with
        ``solo_standings_list``, then shows the notebook and hides the
        standalone list. A SOLO ride associates *solo* with
        ``standings_list`` and hides the notebook. The list that is not
        in use is hidden so its sizer slot collapses.

        See ``ui.views._support.associate_model``'s docstring for
        why this repaints explicitly (unverified remedy).
        """
        if self.entry_mode is EntryMode.MIXED:
            self._teams_model = StandingsListModel(teams)
            self._solo_model = StandingsListModel(solo)
            associate_model(self.teams_standings_list, self._teams_model)
            associate_model(self.solo_standings_list, self._solo_model)
            self.standings_list.Hide()
            self.results_notebook.Show()
        else:
            self._model = StandingsListModel(solo)
            associate_model(self.standings_list, self._model)
            self.results_notebook.Hide()
            self.standings_list.Show()
        self.dialog.Layout()

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
        self.dialog.Layout()

    def show_publish_options(self, options: ExportOptions) -> None:
        """Reflect the five publish checkboxes (``ResultsView``).

        ``SetValue`` fires no ``EVT_CHECKBOX`` (measured), so this
        cannot loop back into the presenter.
        """
        self.show_times_chk.SetValue(options.show_times)
        self.laps_board_chk.SetValue(options.laps_board)
        self.time_board_chk.SetValue(options.time_board)
        self.full_field_chk.SetValue(options.full_field)
        self.all_cards_chk.SetValue(options.all_cards)
        self._apply_show_times_state()

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
        """Force the measured width floor, then Fit() the rest (D16).

        See :meth:`ride_library.RideLibrary._apply_min_size`'s
        docstring for the measured ``SetMinSize`` + ``Fit()``
        reasoning this mirrors.
        """
        self.dialog.SetMinSize(wx.Size(MIN_SIZE[0], -1))
        self.dialog.Fit()
