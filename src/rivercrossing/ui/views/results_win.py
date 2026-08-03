# SPDX-License-Identifier: GPL-3.0-only
"""``ResultsWindow``: ``results_frame`` (1f), standings (E1.5.2).

xrc-windows.md section D's code-side footnote puts
``standings_list``'s columns and rows in code -- ``results.xrc``'s
own header explains why (``wxDataViewListCtrl`` would overwrite the
frozen name). This module is that binding.

The canvas's "Best 5" cell is plain text carrying suit glyphs ("K♠
K♣ K♦ JK★ 9♥"), confirmed against ``design/docs-html``'s own table
markup (a literal ``<td>`` string, not five drawn bitmaps) -- unlike
``main_frame.py``'s Card column or ``entry_detail.py``'s cards_list/
laps_list, ``standings_list`` needs no ``DataViewBitmapRenderer``.
:func:`format_best5` is the pure text formatter this column uses.

Live re-ranking on ``tiebreak_list`` reorder, the stale-export
InfoBar, and the publish-checkbox *actions* are a later phase's job
per ``ResultsPresenter``'s own docstring ("Phase 5 wires...") and are
not in this task's scope -- the five publish checkboxes already
carry their canvas defaults in ``results.xrc`` itself (times off,
laps board on, time board off, full field on, all cards on), so this
module does not set them; it only renders ``standings_list``.

``_find`` is now shared via ``ui.views._support.find_control`` --
see that module's docstring for why it used to be duplicated here.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.dataview

from rivercrossing.ui import ids
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
    "ResultsWindow",
    "StandingsListModel",
    "format_best5",
    "format_card",
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

# D16: the canvas draws this window at 720px; XRC has no window-level
# minsize (results.xrc's own header notes this and defers to code).
# Height is Fit()'s own measurement of the real, demo-populated
# sizer content -- see this task's own report for how it was measured.
MIN_SIZE = (720, 442)


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


_TEXT_ACCESSORS: tuple[Callable[[StandingsRow], str], ...] = (
    lambda standing: str(standing.place),
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

    Implements ``ResultsView.show_standings`` (module-skeletons.md's
    presenter contract); live re-ranking, the stale-export InfoBar
    and the publish-option actions are a later phase's job and are
    not in this task's scope.
    """

    def __init__(self, frame: wx.Frame, *, data_source: DataSource) -> None:
        """Decorate an already-loaded ``results_frame`` window.

        Args:
            frame: The ``wx.Frame`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``results.xrc``.
            data_source: The display-data seam. This view knows only
                the :class:`~rivercrossing.ui.presenters.data_source.
                DataSource` Protocol -- the caller wires in whichever
                implementation applies.
        """
        self.frame = frame
        self.data_source = data_source

        self.standings_list = self._find(ids.STANDINGS_LIST, wx.dataview.DataViewCtrl)
        self.show_times_chk = self._find(ids.SHOW_TIMES_CHK, wx.CheckBox)
        self.laps_board_chk = self._find(ids.LAPS_BOARD_CHK, wx.CheckBox)
        self.time_board_chk = self._find(ids.TIME_BOARD_CHK, wx.CheckBox)
        self.full_field_chk = self._find(ids.FULL_FIELD_CHK, wx.CheckBox)
        self.all_cards_chk = self._find(ids.ALL_CARDS_CHK, wx.CheckBox)

        self._build_columns()
        self._model: StandingsListModel | None = None

        self.show_standings(self.data_source.standings())
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

    def _build_columns(self) -> None:
        """Append ``standings_list``'s seven columns in canvas order."""
        for col, label in enumerate(COLUMN_LABELS):
            self.standings_list.AppendTextColumn(label, col)

    def show_standings(self, rows: list[StandingsRow]) -> None:
        """Render ``standings_list`` (``ResultsView``).

        See ``ui.views._support.associate_model``'s docstring for
        why this repaints explicitly (unverified remedy).
        """
        self._model = StandingsListModel(rows)
        associate_model(self.standings_list, self._model)

    def _apply_min_size(self) -> None:
        """Force the canvas's 720px floor, then Fit() the rest (D16).

        See :meth:`ride_library.RideLibrary._apply_min_size`'s
        docstring for the measured ``SetMinSize`` + ``Fit()``
        reasoning this mirrors.
        """
        self.frame.SetMinSize(wx.Size(MIN_SIZE[0], -1))
        self.frame.Fit()
