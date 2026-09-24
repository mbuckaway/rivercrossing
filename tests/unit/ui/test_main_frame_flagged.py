# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the console Needs Review tab (main_frame).

The review notebook's "Needs Review" tab lists every crossing that needs
the operator's attention: a **held** card (hold mode, R-34) awaiting a
confirm/void decision, a **credited** short lap (always-deal mode) whose
crossing detail the operator opens, and both halves of a live
**duplicate** pair (Phase 3). Six columns name each row -- Issue |
Card | Plate | Lap | Lap time | Rider -- and the "Show Held Cards Only"
box narrows the tab to the rows that still hold a card.

The tab's ``flagged_list`` and the sidebar's ``review_btn`` hand the app
the activated row's plate, its card disposition (``card_status``) and
its duplicate bit, which is why ``FlaggedListModel`` exposes
``card_status_for_row``/``duplicate_for_row`` and the seam fires
``callback(plate, card_status, duplicate)``.

This module pins the view half headlessly, the way
``test_main_frame_riders_list.py`` does: the pure model is constructed
directly (a ``DataViewIndexListModel`` subclass needs no display), and
the activation and filter handlers run as unbound methods against a
shell owning only the state each one reads -- so no ``wx`` window is
ever built. What genuinely needs a live control (the real
``Bind``-delivered events) is not pinned here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import wx

from rivercrossing.ui.presenters.data_source import FeedRow
from rivercrossing.ui.views import main_frame

if TYPE_CHECKING:
    from collections.abc import Sequence


def _row(  # noqa: PLR0913 -- one keyword per feed field a test varies
    *,
    plate: str = "12",
    lap: int = 3,
    lap_time: str = "0:05",
    entry: str = "Rider 12",
    team: str = "Trail Blazers",
    card_status: str = "credited",
    flagged: bool = True,
    held: bool = False,
    duplicate: bool = False,
    team_overlap: bool = False,
) -> FeedRow:
    """Build the review row the flagged list wraps."""
    return FeedRow(
        time="10:03:20",
        plate=plate,
        entry=entry,
        lap=lap,
        lap_time=lap_time,
        total="0:05",
        card="9H",
        team=team,
        flagged=flagged,
        held=held,
        card_status=card_status,
        duplicate=duplicate,
        team_overlap=team_overlap,
    )


def _held_row(plate: str = "12") -> FeedRow:
    """Return a hold-mode short lap: flagged, its card held (R-34)."""
    return _row(plate=plate, held=True, card_status="held")


def _credited_row(plate: str = "34") -> FeedRow:
    """Return an always-deal short lap: flagged, its card credited."""
    return _row(plate=plate, card_status="credited")


def _voided_row(plate: str = "56") -> FeedRow:
    """Return a short lap whose held card was voided."""
    return _row(plate=plate, card_status="voided")


def _duplicate_row(plate: str = "78") -> FeedRow:
    """Return one half of a live duplicate pair."""
    return _row(plate=plate, flagged=False, card_status="", duplicate=True)


def _team_overlap_row(plate: str = "45") -> FeedRow:
    """Return a TEAM entry's flagged crossing: a team overlap."""
    return _row(plate=plate, team="Dirt Dynamos", team_overlap=True)


# ------------------------------------------------------------ the model


def test_flagged_list_model_keeps_the_six_review_columns() -> None:
    """WS-H: the Issue cell leads, the other five name the crossing."""
    model = main_frame.FlaggedListModel([_row()])

    assert (model.GetColumnCount(), main_frame.FLAG_COLUMN_LABELS) == (
        6,
        ("Issue", "Card", "Plate", "Lap", "Lap time", "Rider"),
    )
    assert main_frame.FLAG_COL_ISSUE == 0


def test_flagged_column_indexes_given_the_canvas_order_name_each_cell() -> None:
    """T-4: the six column constants are the six canvas positions."""
    assert (
        main_frame.FLAG_COL_ISSUE,
        main_frame.FLAG_COL_CARD,
        main_frame.FLAG_COL_PLATE,
        main_frame.FLAG_COL_LAP,
        main_frame.FLAG_COL_LAP_TIME,
        main_frame.FLAG_COL_RIDER,
    ) == (0, 1, 2, 3, 4, 5)


def test_flagged_column_widths_given_the_six_labels_pin_one_width_each() -> None:
    """Every column carries its own width, so no cell truncates."""
    assert main_frame.FLAG_COLUMN_WIDTHS == (136, 76, 64, 52, 48, 256)
    assert len(main_frame.FLAG_COLUMN_WIDTHS) == len(main_frame.FLAG_COLUMN_LABELS)


@pytest.mark.parametrize(
    ("col", "expected"),
    [
        (0, "Short lap"),
        (1, "Credited"),
        (2, "12"),
        (3, "3"),
        (4, "0:05"),
        (5, "Rider 12"),
    ],
    ids=["issue", "card", "plate", "lap", "lap-time", "rider"],
)
def test_flagged_list_model_given_a_credited_row_renders_its_six_cells(
    col: int, expected: str
) -> None:
    """Every column reads its own field off the wrapped row."""
    model = main_frame.FlaggedListModel([_row()])

    assert model.GetValueByRow(0, col) == expected


def test_flagged_list_model_given_a_distinct_lap_time_shows_that_cell() -> None:
    """T-4: the cell reads ``lap_time``, not the row's total."""
    model = main_frame.FlaggedListModel([_row(lap_time="0:07")])

    assert model.GetValueByRow(0, main_frame.FLAG_COL_LAP_TIME) == "0:07"


def test_flagged_list_model_given_a_miss_row_shows_an_empty_lap_time_cell() -> None:
    """T-4: a miss row carries no lap time, so its cell is blank."""
    model = main_frame.FlaggedListModel([_row(lap_time="", flagged=False)])

    assert model.GetValueByRow(0, main_frame.FLAG_COL_LAP_TIME) == ""


@pytest.mark.parametrize(
    ("card_status", "expected"),
    [("held", "Held"), ("credited", "Credited"), ("voided", "Void"), ("", "")],
    ids=["held", "credited", "voided", "undealt"],
)
def test_flagged_list_model_card_column_given_a_disposition_shows_its_word(
    card_status: str, expected: str
) -> None:
    """The Card cell names the disposition, blank with no card."""
    model = main_frame.FlaggedListModel([_row(card_status=card_status, flagged=False)])

    assert model.GetValueByRow(0, main_frame.FLAG_COL_CARD) == expected


@pytest.mark.parametrize("card_status", ["held", "credited", "voided", ""])
def test_flagged_list_model_given_a_row_reports_its_card_status(card_status: str) -> None:
    """``card_status_for_row`` answers the row's own token."""
    model = main_frame.FlaggedListModel([_row(card_status=card_status)])

    assert model.card_status_for_row(0) == card_status


@pytest.mark.parametrize(("duplicate", "expected"), [(True, True), (False, False)])
def test_flagged_list_model_given_a_row_reports_whether_it_duplicates(
    duplicate: bool,  # noqa: FBT001 -- a parametrize row's value, not a call-site bool
    expected: bool,  # noqa: FBT001 -- a parametrize row's value, not a call-site bool
) -> None:
    """``duplicate_for_row`` answers the row's own duplicate bit."""
    model = main_frame.FlaggedListModel([_row(duplicate=duplicate)])

    assert model.duplicate_for_row(0) is expected


def test_flagged_list_model_given_a_duplicate_row_shows_the_duplicate_issue() -> None:
    """A live duplicate pair's row says why it is in Needs Review."""
    model = main_frame.FlaggedListModel([_duplicate_row()])

    assert model.GetValueByRow(0, main_frame.FLAG_COL_ISSUE) == "Duplicate crossing"


def test_flagged_list_model_given_a_held_short_lap_shows_the_short_lap_issue() -> None:
    """The Card column carries the hold: the Issue cell does not."""
    model = main_frame.FlaggedListModel([_held_row()])

    assert model.GetValueByRow(0, main_frame.FLAG_COL_ISSUE) == "Short lap"


def test_flagged_list_model_given_a_credited_short_lap_shows_the_short_lap_issue() -> None:
    """Always-deal: a credited short lap is flagged, not held."""
    model = main_frame.FlaggedListModel([_credited_row()])

    assert model.GetValueByRow(0, main_frame.FLAG_COL_ISSUE) == "Short lap"


def test_flagged_list_model_given_a_team_overlap_row_shows_the_team_overlap_issue() -> None:
    """A flagged TEAM crossing reads "Team overlap", not "Short lap"."""
    model = main_frame.FlaggedListModel([_team_overlap_row()])

    assert model.GetValueByRow(0, main_frame.FLAG_COL_ISSUE) == "Team overlap"


@pytest.mark.parametrize(
    ("rows", "expected_count"),
    [([], 0), ([_row()], 1), ([_row(), _row(), _row()], 3)],
    ids=["empty", "single", "many"],
)
def test_flagged_list_model_given_rows_keeps_the_six_columns_for_every_size(
    rows: list[FeedRow], expected_count: int
) -> None:
    """T-4: an empty review tab is still a six-column model."""
    model = main_frame.FlaggedListModel(rows)

    assert (
        model.GetCount(),
        model.GetColumnCount(),
        len(main_frame.FLAG_COLUMN_LABELS),
    ) == (expected_count, 6, 6)


# ---------------------------------------------------------- the columns


class _ColumnRecorder:
    """A ``flagged_list`` double recording each appended column."""

    def __init__(self) -> None:
        """Start with no recorded columns."""
        self.calls: list[tuple[str, int, int]] = []

    def AppendTextColumn(self, label: str, col: int, *, width: int) -> str:  # noqa: N802
        """Record ``(label, column, width)`` and return a handle."""
        self.calls.append((label, col, width))
        return label


class _ColumnShell:
    """A ``MainFrame`` double owning only the appended-to control."""

    def __init__(self) -> None:
        """Build the recording control."""
        self.flagged_list = _ColumnRecorder()


def test_build_flagged_columns_given_the_six_labels_appends_each_with_its_width() -> None:
    """``_build_flagged_columns`` pins every column to a width.

    Every column here is plain text -- including Card: the review tab's
    Card cell is a disposition word ("Held"/"Credited"/"Void"), not a
    card face, so it has no suit to colour and takes no markup renderer
    (unlike the console feed's own Card column, which holds the dealt
    card's glyph). ``test_flagged_cell_cards_*`` pins that wording.
    """
    shell = _ColumnShell()

    columns = main_frame.MainFrame._build_flagged_columns(shell)

    assert shell.flagged_list.calls == [
        ("Issue", 0, 136),
        ("Card", 1, 76),
        ("Plate", 2, 64),
        ("Lap", 3, 52),
        ("Lap time", 4, 48),
        ("Rider", 5, 256),
    ]
    assert columns == ("Issue", "Card", "Plate", "Lap", "Lap time", "Rider")


# ------------------------------------------------------------ doubles


class _Item:
    """A ``wx.dataview.DataViewItem`` stand-in the event carries."""


class _Event:
    """A ``wx.dataview.DataViewEvent`` double with one item."""

    def __init__(self) -> None:
        """Carry one opaque item; the model double ignores it."""
        self._item = _Item()

    def GetItem(self) -> object:  # noqa: N802 -- wx API name the double mirrors
        """Return the activated item."""
        return self._item


class _Recorder:
    """The open-flagged callback double: records each firing."""

    def __init__(self) -> None:
        """Start with nothing fired."""
        self.fired: list[tuple[str, str, bool]] = []

    def __call__(
        self,
        plate: str,
        card_status: str,
        duplicate: bool,  # noqa: FBT001 -- mirrors the seam's own positional bool
    ) -> None:
        """Append one ``(plate, card_status, duplicate)`` firing."""
        self.fired.append((plate, card_status, duplicate))


class _FlaggedModel:
    """A ``FlaggedListModel`` double owning the rows a test supplies."""

    def __init__(self, rows: Sequence[FeedRow], *, wx_row: int | None = 0) -> None:
        """Wrap *rows*; ``wx_row=None`` models wx's NOT_FOUND answer."""
        self._rows = tuple(rows)
        self._wx_row = wx.NOT_FOUND if wx_row is None else wx_row
        self.resolved: list[int] = []

    def GetRow(self, _item: object) -> int:  # noqa: N802 -- wx API name
        """Return the configured model row, or wx's NOT_FOUND."""
        return self._wx_row

    def GetValueByRow(self, row: int, col: int) -> str:  # noqa: N802 -- wx API name
        """Record the resolution and return the requested cell."""
        self.resolved.append(row)
        if col == main_frame.FLAG_COL_PLATE:
            return self._rows[row].plate
        return ""

    def card_status_for_row(self, row: int) -> str:
        """Return the row's card disposition token."""
        return self._rows[row].card_status

    def duplicate_for_row(self, row: int) -> bool:
        """Return the row's duplicate bit."""
        return self._rows[row].duplicate


class _Notebook:
    """A ``wx.Notebook`` double carrying page labels."""

    def __init__(self, *labels: str) -> None:
        """Start with one page per label, in order."""
        self._labels = labels
        self.selection: int | None = None

    def GetPageCount(self) -> int:  # noqa: N802 -- wx API name
        """Return the number of pages."""
        return len(self._labels)

    def GetPageText(self, index: int) -> str:  # noqa: N802 -- wx API name
        """Return page *index*'s label."""
        return self._labels[index]

    def SetSelection(self, index: int) -> None:  # noqa: N802 -- wx API name
        """Record the selected page."""
        self.selection = index


class _Selection:
    """A ``wx.dataview.DataViewItem`` double for the selection.

    ``GetSelection()`` answers an invalid item when nothing is selected,
    which is why the handler asks ``IsOk()`` before resolving the row.
    """

    def __init__(self, *, ok: bool = True) -> None:
        """Carry whether the item references a real row."""
        self._ok = ok

    def IsOk(self) -> bool:  # noqa: N802 -- wx API name the double mirrors
        """Return whether the item references a real row."""
        return self._ok


class _FlaggedList:
    """A ``wx.dataview.DataViewCtrl`` double for ``flagged_list``.

    ``main.xrc`` authors the control as a plain ``DataViewCtrl``, whose
    selection surface is ``GetSelection()`` -> ``DataViewItem``. A
    ``GetSelectedRow`` is a ``wxDataViewListCtrl`` method and does not
    exist here, so the double omits it: a regression to the list-ctrl
    API then fails in this suite rather than in the operator's hands.
    """

    def __init__(self, *, selected: int = wx.NOT_FOUND) -> None:
        """Carry the control's current selection and a focus count."""
        self.selected = selected
        self.focused = 0

    def SetFocus(self) -> None:  # noqa: N802 -- wx API name
        """Record one focus request."""
        self.focused += 1

    def GetSelection(self) -> _Selection:  # noqa: N802 -- wx API name
        """Return the selected item, invalid when no row is selected."""
        return _Selection(ok=self.selected != wx.NOT_FOUND)


class _Shell:
    """A ``MainFrame`` double owning only what the routing reads.

    ``_on_flagged_activated``, ``_on_review_clicked`` and
    ``focus_review_panel`` are invoked here as unbound methods against
    this shell, so their wiring is pinned without building a real frame.
    """

    def __init__(
        self,
        *,
        model: _FlaggedModel | None = None,
        callback: object = None,
        selected: int = wx.NOT_FOUND,
    ) -> None:
        """Store the state the three methods under test read."""
        self._flagged_model = model
        self._on_open_flagged = callback
        self.review_notebook = _Notebook(
            main_frame.RIDERS_PAGE_LABEL, main_frame.NEEDS_REVIEW_PAGE_LABEL
        )
        self.flagged_list = _FlaggedList(selected=selected)

    def focus_review_panel(self) -> None:
        """Run the real focus-only handler."""
        main_frame.MainFrame.focus_review_panel(self)

    def _fire_open_flagged(self, row: int) -> None:
        """Run the real row-routing helper."""
        main_frame.MainFrame._fire_open_flagged(self, row)


# ------------------------------------------- flagged activation (view)


def test_on_flagged_activated_given_a_held_row_fires_plate_status_and_not_duplicate() -> None:
    """A held row's activation fires ``(plate, "held", False)``."""
    recorder = _Recorder()
    shell = _Shell(
        model=_FlaggedModel([_held_row(plate="12")]),
        callback=recorder,
    )

    main_frame.MainFrame._on_flagged_activated(shell, _Event())

    assert recorder.fired == [("12", "held", False)]


def test_on_flagged_activated_given_a_credited_row_fires_the_credited_status() -> None:
    """A credited short lap's activation fires the credited status."""
    recorder = _Recorder()
    shell = _Shell(
        model=_FlaggedModel([_credited_row(plate="34")]),
        callback=recorder,
    )

    main_frame.MainFrame._on_flagged_activated(shell, _Event())

    assert recorder.fired == [("34", "credited", False)]


def test_on_flagged_activated_given_a_duplicate_row_fires_the_duplicate_bit() -> None:
    """A duplicate pair's row fires ``(plate, "", True)``."""
    recorder = _Recorder()
    shell = _Shell(
        model=_FlaggedModel([_duplicate_row(plate="78")]),
        callback=recorder,
    )

    main_frame.MainFrame._on_flagged_activated(shell, _Event())

    assert recorder.fired == [("78", "", True)]


def test_on_flagged_activated_given_no_model_fires_nothing() -> None:
    """Rows never rendered mean no row can activate."""
    recorder = _Recorder()
    shell = _Shell(callback=recorder)

    main_frame.MainFrame._on_flagged_activated(shell, _Event())

    assert recorder.fired == []


def test_on_flagged_activated_given_wx_not_found_row_fires_nothing() -> None:
    """A stale activation resolves to no row, so nothing fires."""
    recorder = _Recorder()
    shell = _Shell(
        model=_FlaggedModel([_row()], wx_row=None),
        callback=recorder,
    )

    main_frame.MainFrame._on_flagged_activated(shell, _Event())

    assert recorder.fired == []


def test_on_flagged_activated_given_no_callback_never_resolves_the_row() -> None:
    """An app-unwired console leaves the row untouched."""
    model = _FlaggedModel([_row()])
    shell = _Shell(model=model)

    main_frame.MainFrame._on_flagged_activated(shell, _Event())

    assert model.resolved == []


def test_set_on_open_flagged_given_a_wired_callback_fires_all_three_parts() -> None:
    """Wiring stores the callback; firing hands it all three."""
    recorder = _Recorder()
    shell = _Shell(model=main_frame.FlaggedListModel([_held_row(plate="12")]))
    main_frame.MainFrame.set_on_open_flagged(shell, recorder)

    shell._fire_open_flagged(0)

    assert (shell._on_open_flagged, recorder.fired) == (recorder, [("12", "held", False)])


# -------------------------------------------------- review_btn routing


def test_on_review_clicked_given_a_selected_held_row_focuses_and_fires_the_seam() -> None:
    """Review… focuses the tab, then acts on the selected row."""
    recorder = _Recorder()
    shell = _Shell(
        model=_FlaggedModel([_held_row(plate="12")]),
        callback=recorder,
        selected=0,
    )

    main_frame.MainFrame._on_review_clicked(shell)

    assert (shell.review_notebook.selection, shell.flagged_list.focused, recorder.fired) == (
        1,
        1,
        [("12", "held", False)],
    )


def test_on_review_clicked_given_no_selection_only_focuses() -> None:
    """Nothing selected means Review… only focuses."""
    recorder = _Recorder()
    shell = _Shell(
        model=_FlaggedModel([_row()]),
        callback=recorder,
        selected=wx.NOT_FOUND,
    )

    main_frame.MainFrame._on_review_clicked(shell)

    assert (shell.review_notebook.selection, shell.flagged_list.focused, recorder.fired) == (
        1,
        1,
        [],
    )


def test_on_review_clicked_given_no_model_only_focuses() -> None:
    """A model-less console has no row to act on."""
    recorder = _Recorder()
    shell = _Shell(callback=recorder, selected=0)

    main_frame.MainFrame._on_review_clicked(shell)

    assert (shell.flagged_list.focused, recorder.fired) == (1, [])


def test_on_review_clicked_given_a_stale_selection_only_focuses() -> None:
    """A selected item that resolves to no row acts on nothing."""
    recorder = _Recorder()
    shell = _Shell(
        model=_FlaggedModel([_row()], wx_row=None),
        callback=recorder,
        selected=0,
    )

    main_frame.MainFrame._on_review_clicked(shell)

    assert (shell.review_notebook.selection, shell.flagged_list.focused, recorder.fired) == (
        1,
        1,
        [],
    )


def test_focus_review_panel_given_a_selected_row_fires_no_seam() -> None:
    """The focus-only panel jump fires no row-activation seam.

    The console's Review… button and ``focus_review_panel`` both land
    on the same page and stop there: focusing never activates a row,
    so the card-review seam stays untouched.
    """
    recorder = _Recorder()
    shell = _Shell(
        model=_FlaggedModel([_row()]),
        callback=recorder,
        selected=0,
    )

    main_frame.MainFrame.focus_review_panel(shell)

    assert (shell.review_notebook.selection, shell.flagged_list.focused, recorder.fired) == (
        1,
        1,
        [],
    )


def test_focus_review_panel_given_no_review_page_returns_without_focus() -> None:
    """A notebook without the Needs Review page has nothing to focus."""
    shell = _Shell(model=_FlaggedModel([_row()]), selected=0)
    shell.review_notebook = _Notebook(main_frame.RIDERS_PAGE_LABEL)

    main_frame.MainFrame.focus_review_panel(shell)

    assert (shell.review_notebook.selection, shell.flagged_list.focused) == (None, 0)


# ------------------------------------------------ Show Held Cards Only


class _AssociateRecorder:
    """A ``flagged_list`` double recording the model it is handed."""

    def __init__(self) -> None:
        """Start with no associated model."""
        self.model: object = None

    def AssociateModel(self, model: object) -> None:  # noqa: N802 -- wx API name
        """Record the associated model."""
        self.model = model


class _FilterShell:
    """A ``MainFrame`` double owning the rows it filters."""

    def __init__(self, *, show_held_only: bool = False) -> None:
        """Start with the box's own state and an empty render."""
        self._show_held_only = show_held_only
        self._flagged_rows: list[FeedRow] = []
        self._flagged_model: main_frame.FlaggedListModel | None = None
        self.flagged_list = _AssociateRecorder()

    def show_flagged(self, rows: list[FeedRow]) -> None:
        """Run the real render against this shell's own state."""
        main_frame.MainFrame.show_flagged(self, rows)


@pytest.mark.parametrize(
    ("show_held_only", "rows", "expected_count"),
    [
        (False, [], 0),
        (False, [_credited_row()], 1),
        (False, [_held_row(), _credited_row(), _voided_row(), _duplicate_row()], 4),
        (True, [], 0),
        (True, [_credited_row()], 0),
        (True, [_held_row(), _credited_row(), _voided_row(), _duplicate_row()], 2),
    ],
    ids=["off-empty", "off-single", "off-many", "on-empty", "on-no-hold", "on-many"],
)
def test_show_flagged_given_the_held_only_box_filters_the_rendered_rows(
    show_held_only: bool,  # noqa: FBT001 -- a parametrize row's value, not a call-site bool
    rows: list[FeedRow],
    expected_count: int,
) -> None:
    """T-13: the box narrows the tab to held cards and duplicates."""
    shell = _FilterShell(show_held_only=show_held_only)

    main_frame.MainFrame.show_flagged(shell, rows)

    assert (shell._flagged_rows, shell._flagged_model.GetCount()) == (rows, expected_count)


def test_show_flagged_given_the_held_only_box_unchecked_keeps_every_row_in_order() -> None:
    """Unchecked, the tab renders every row the caller supplied."""
    shell = _FilterShell(show_held_only=False)
    rows = [_held_row("12"), _credited_row("34"), _voided_row("56"), _duplicate_row("78")]

    main_frame.MainFrame.show_flagged(shell, rows)

    model = shell._flagged_model
    assert (
        model.GetValueByRow(0, main_frame.FLAG_COL_PLATE),
        model.GetValueByRow(1, main_frame.FLAG_COL_PLATE),
        model.GetValueByRow(2, main_frame.FLAG_COL_PLATE),
        model.GetValueByRow(3, main_frame.FLAG_COL_PLATE),
    ) == ("12", "34", "56", "78")


def test_show_flagged_given_the_held_only_box_checked_keeps_held_and_duplicate_rows() -> None:
    """Checked, the tab renders exactly the held/duplicate rows."""
    shell = _FilterShell(show_held_only=True)
    rows = [_held_row("12"), _credited_row("34"), _duplicate_row("78")]

    main_frame.MainFrame.show_flagged(shell, rows)

    model = shell._flagged_model
    assert (
        model.GetCount(),
        model.GetValueByRow(0, main_frame.FLAG_COL_PLATE),
        model.GetValueByRow(1, main_frame.FLAG_COL_PLATE),
    ) == (2, "12", "78")


class _CheckBox:
    """A ``wx.CheckBox`` double carrying its own value."""

    def __init__(self, *, checked: bool) -> None:
        """Carry the box's current state."""
        self._checked = checked

    def GetValue(self) -> bool:  # noqa: N802 -- wx API name
        """Return the box's current state."""
        return self._checked


class _CheckEvent:
    """A ``wx.CommandEvent`` double recording ``Skip()``."""

    def __init__(self) -> None:
        """Start with nothing skipped."""
        self.skipped = 0

    def Skip(self) -> None:  # noqa: N802 -- wx API name
        """Record one Skip request."""
        self.skipped += 1


class _ToggleShell(_FilterShell):
    """A ``MainFrame`` double owning the held-only checkbox too."""

    def __init__(self, *, checked: bool) -> None:
        """Start with the box out of step with ``_show_held_only``."""
        super().__init__(show_held_only=not checked)
        self.show_held_only_chk = _CheckBox(checked=checked)
        self.rerendered: list[list[FeedRow]] = []

    def show_flagged(self, rows: list[FeedRow]) -> None:
        """Record the re-render, then run the real render."""
        self.rerendered.append(rows)
        main_frame.MainFrame.show_flagged(self, rows)


@pytest.mark.parametrize(
    ("checked", "expected_held_only", "expected_count"),
    [(True, True, 1), (False, False, 2)],
    ids=["checked", "unchecked"],
)
def test_on_show_held_only_changed_given_the_box_rerenders_from_the_stored_rows(
    checked: bool,  # noqa: FBT001 -- a parametrize row's value, not a call-site bool
    expected_held_only: bool,  # noqa: FBT001 -- a parametrize row's value, not a call-site bool
    expected_count: int,
) -> None:
    """The handler copies the box, then re-filters the rows it holds."""
    rows = [_held_row(), _credited_row()]
    shell = _ToggleShell(checked=checked)
    shell._flagged_rows = list(rows)
    event = _CheckEvent()

    main_frame.MainFrame._on_show_held_only_changed(shell, event)

    assert (
        shell._show_held_only,
        shell.rerendered,
        shell._flagged_model.GetCount(),
        event.skipped,
    ) == (expected_held_only, [rows], expected_count, 1)
