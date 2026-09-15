# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the console Needs Review routing (main_frame).

The review notebook's "Needs Review" tab now carries two kinds of short
lap: a **held** card (hold mode, R-34) awaiting a confirm/void decision
and a **credited** short lap (always-deal mode) whose crossing detail
the operator opens. The tab's ``flagged_list`` and the sidebar's
``review_btn`` must therefore hand the app the activated row's plate
*and* its ``held`` flag, which is why ``FlaggedListModel`` exposes
``held_for_row`` and the seam fires ``callback(plate, held)``.

This module pins the view half headlessly, the way
``test_main_frame_riders_list.py`` does: the pure model is constructed
directly (a ``DataViewIndexListModel`` subclass needs no display), and
the two activation handlers run as unbound methods against a shell
owning only the state each one reads -- so no ``wx`` window is ever
built. What genuinely needs a live control (the real ``Bind``-delivered
events) is not pinned here.
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
    flagged: bool = True,
    held: bool = False,
    duplicate: bool = False,
) -> FeedRow:
    """Build the short-lap row the review list wraps."""
    return FeedRow(
        time="10:03:20",
        plate=plate,
        entry="Rider 12",
        lap=lap,
        lap_time="0:05",
        total="0:05",
        card="9H",
        flagged=flagged,
        held=held,
        duplicate=duplicate,
    )


# ------------------------------------------------------------ the model


def test_flagged_list_model_given_a_held_row_reports_held() -> None:
    """A held crossing's row answers ``held_for_row`` True."""
    model = main_frame.FlaggedListModel([_row(held=True)])

    assert model.held_for_row(0) is True


def test_flagged_list_model_given_a_credited_short_lap_reports_not_held() -> None:
    """An always-deal short lap's row answers ``held_for_row`` False."""
    model = main_frame.FlaggedListModel([_row(flagged=True, held=False)])

    assert model.held_for_row(0) is False


def test_flagged_list_model_keeps_the_four_review_columns() -> None:
    """WS-H: the Issue reason joins the three routing cells."""
    model = main_frame.FlaggedListModel([_row()])

    assert (model.GetColumnCount(), main_frame.FLAG_COLUMN_LABELS) == (
        4,
        ("Plate", "Lap", "Lap time", "Issue"),
    )
    assert main_frame.FLAG_COL_ISSUE == 3


def test_flagged_list_model_given_a_duplicate_row_shows_the_duplicate_issue() -> None:
    """A live duplicate pair's row says why it is in Needs Review."""
    model = main_frame.FlaggedListModel([_row(flagged=False, duplicate=True)])

    assert model.GetValueByRow(0, main_frame.FLAG_COL_ISSUE) == "Duplicate crossing"


def test_flagged_list_model_given_a_held_short_lap_shows_the_card_held_issue() -> None:
    """A held card's row names the hold, not just the short lap."""
    model = main_frame.FlaggedListModel([_row(flagged=True, held=True)])

    assert model.GetValueByRow(0, main_frame.FLAG_COL_ISSUE) == "Short lap — card held"


def test_flagged_list_model_given_a_credited_short_lap_shows_the_short_lap_issue() -> None:
    """Always-deal: a credited short lap is flagged, not held."""
    model = main_frame.FlaggedListModel([_row(flagged=True, held=False)])

    assert model.GetValueByRow(0, main_frame.FLAG_COL_ISSUE) == "Short lap"


@pytest.mark.parametrize(
    ("rows", "expected_count"),
    [([], 0), ([_row()], 1), ([_row(), _row(), _row()], 3)],
    ids=["empty", "single", "many"],
)
def test_flagged_list_model_given_rows_keeps_the_four_columns_for_every_size(
    rows: list[FeedRow], expected_count: int
) -> None:
    """T-4: an empty review tab is still a four-column model."""
    model = main_frame.FlaggedListModel(rows)

    assert (model.GetCount(), model.GetColumnCount()) == (expected_count, 4)


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

    def GetValueByRow(self, row: int, _col: int) -> str:  # noqa: N802 -- wx API name
        """Record the resolution and return the row's plate."""
        self.resolved.append(row)
        return self._rows[row].plate

    def held_for_row(self, row: int) -> bool:
        """Return the row's held flag."""
        return self._rows[row].held


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


def test_on_flagged_activated_given_a_held_row_fires_the_seam_with_plate_and_held() -> None:
    """A held row's activation hands the app ``(plate, True)``."""
    fired: list[tuple[str, bool]] = []
    shell = _Shell(
        model=_FlaggedModel([_row(plate="12", held=True)]),
        callback=lambda plate, held: fired.append((plate, held)),
    )

    main_frame.MainFrame._on_flagged_activated(shell, _Event())

    assert fired == [("12", True)]


def test_on_flagged_activated_given_a_credited_row_fires_the_seam_not_held() -> None:
    """A credited short lap's activation fires ``(plate, False)``."""
    fired: list[tuple[str, bool]] = []
    shell = _Shell(
        model=_FlaggedModel([_row(plate="34", held=False)]),
        callback=lambda plate, held: fired.append((plate, held)),
    )

    main_frame.MainFrame._on_flagged_activated(shell, _Event())

    assert fired == [("34", False)]


def test_on_flagged_activated_given_no_model_fires_nothing() -> None:
    """Rows never rendered mean no row can activate."""
    fired: list[tuple[str, bool]] = []
    shell = _Shell(callback=lambda plate, held: fired.append((plate, held)))

    main_frame.MainFrame._on_flagged_activated(shell, _Event())

    assert fired == []


def test_on_flagged_activated_given_wx_not_found_row_fires_nothing() -> None:
    """A stale activation resolves to no row, so nothing fires."""
    fired: list[tuple[str, bool]] = []
    shell = _Shell(
        model=_FlaggedModel([_row()], wx_row=None),
        callback=lambda plate, held: fired.append((plate, held)),
    )

    main_frame.MainFrame._on_flagged_activated(shell, _Event())

    assert fired == []


def test_on_flagged_activated_given_no_callback_never_resolves_the_row() -> None:
    """An app-unwired console leaves the row untouched."""
    model = _FlaggedModel([_row()])
    shell = _Shell(model=model)

    main_frame.MainFrame._on_flagged_activated(shell, _Event())

    assert model.resolved == []


# -------------------------------------------------- review_btn routing


def test_on_review_clicked_given_a_selected_held_row_focuses_and_fires_the_seam() -> None:
    """Review… focuses the tab, then acts on the selected row."""
    fired: list[tuple[str, bool]] = []
    shell = _Shell(
        model=_FlaggedModel([_row(plate="12", held=True)]),
        callback=lambda plate, held: fired.append((plate, held)),
        selected=0,
    )

    main_frame.MainFrame._on_review_clicked(shell)

    assert (shell.review_notebook.selection, shell.flagged_list.focused, fired) == (
        1,
        1,
        [("12", True)],
    )


def test_on_review_clicked_given_no_selection_only_focuses() -> None:
    """Nothing selected means Review… only focuses."""
    fired: list[tuple[str, bool]] = []
    shell = _Shell(
        model=_FlaggedModel([_row()]),
        callback=lambda plate, held: fired.append((plate, held)),
        selected=wx.NOT_FOUND,
    )

    main_frame.MainFrame._on_review_clicked(shell)

    assert (shell.review_notebook.selection, shell.flagged_list.focused, fired) == (1, 1, [])


def test_on_review_clicked_given_no_model_only_focuses() -> None:
    """A model-less console has no row to act on."""
    fired: list[tuple[str, bool]] = []
    shell = _Shell(callback=lambda plate, held: fired.append((plate, held)), selected=0)

    main_frame.MainFrame._on_review_clicked(shell)

    assert (shell.flagged_list.focused, fired) == (1, [])


def test_on_review_clicked_given_a_stale_selection_only_focuses() -> None:
    """A selected item that resolves to no row acts on nothing."""
    fired: list[tuple[str, bool]] = []
    shell = _Shell(
        model=_FlaggedModel([_row()], wx_row=None),
        callback=lambda plate, held: fired.append((plate, held)),
        selected=0,
    )

    main_frame.MainFrame._on_review_clicked(shell)

    assert (shell.review_notebook.selection, shell.flagged_list.focused, fired) == (1, 1, [])


def test_focus_review_panel_given_a_selected_row_fires_no_seam() -> None:
    """The menu route stays focus-only: Cards ▸ Review Held Cards."""
    fired: list[tuple[str, bool]] = []
    shell = _Shell(
        model=_FlaggedModel([_row()]),
        callback=lambda plate, held: fired.append((plate, held)),
        selected=0,
    )

    main_frame.MainFrame.focus_review_panel(shell)

    assert (shell.review_notebook.selection, shell.flagged_list.focused, fired) == (1, 1, [])


def test_focus_review_panel_given_no_review_page_returns_without_focus() -> None:
    """A notebook without the Needs Review page has nothing to focus."""
    shell = _Shell(model=_FlaggedModel([_row()]), selected=0)
    shell.review_notebook = _Notebook(main_frame.RIDERS_PAGE_LABEL)

    main_frame.MainFrame.focus_review_panel(shell)

    assert (shell.review_notebook.selection, shell.flagged_list.focused) == (None, 0)
