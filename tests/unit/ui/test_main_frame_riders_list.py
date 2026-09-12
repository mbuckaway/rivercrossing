# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the console's riders tab (main_frame).

The console's ``console_riders_list`` draws the shared
``CONSOLE_RIDER_COLUMNS`` (Plate | Name | Team | Sex | Cards) and sorts
natively through ``RiderRowListModel.Compare`` -- the same pattern the
team editor, the ride library and the rider editor's own list use; the
review notebook opens on that Riders page.

As with ``test_main_frame_ride_header.py``, only what genuinely needs
no window is pinned here:

- the ``review_notebook`` page order, read straight out of
  ``main.xrc`` as XML;
- the one pure helper the code-side wiring is built from --
  ``_page_index`` -- and the column-label tuple;
- the console's own thin delegates (``show_riders``,
  ``_on_column_sorted``, ``_apply_sort``), driven against a shell that
  owns only the state each one reads.

The rest of the wiring -- the ``Bind`` calls that deliver a real header
sort or select a real notebook page -- needs a live ``wx`` window and
stays with the functional suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from defusedxml.ElementTree import parse

from rivercrossing.ui.presenters.data_source import RiderRow
from rivercrossing.ui.rider_columns import CONSOLE_RIDER_COLUMNS, SOLO_TEAM_TEXT
from rivercrossing.ui.views import main_frame

if TYPE_CHECKING:
    # Type-only: defusedxml does not re-export the Element class.
    # Every parse goes through the defused facade above.
    from xml.etree.ElementTree import Element

XRC_DIR = Path(__file__).resolve().parents[3] / "src" / "rivercrossing" / "ui" / "xrc"

MAIN_XRC = "main.xrc"


# ------------------------------------------------------------ the XRC


def _review_notebook() -> Element:
    """Return ``main.xrc``'s ``review_notebook`` object."""
    root = parse(XRC_DIR / MAIN_XRC).getroot()
    return next(
        obj for obj in root.iter("object") if obj.get("name") == main_frame.REVIEW_NOTEBOOK
    )


def _pages() -> list[Element]:
    """Return the notebook's own ``notebookpage`` children, in order.

    Direct children only, so the list is the page order the loaded
    notebook will have -- page 0 is the default page, which is the
    whole point of this check.
    """
    return [page for page in _review_notebook() if page.get("class") == "notebookpage"]


def _labels() -> list[str]:
    """Return every page's label text, in page order."""
    return [(page.findtext("label") or "") for page in _pages()]


def _control_names(page: Element) -> list[str]:
    """Return every named control declared inside *page*."""
    return [obj.get("name") or "" for obj in page.iter("object") if obj.get("name")]


def test_review_notebook_given_the_xrc_page_order_opens_on_the_riders_page() -> None:
    """Phase 4: the Riders tab is page 0, so it is the default page."""
    assert _labels()[0] == main_frame.RIDERS_PAGE_LABEL


def test_review_notebook_given_the_riders_page_keeps_needs_review_second() -> None:
    """The reorder moves one page: Needs Review follows Riders."""
    assert _labels() == [main_frame.RIDERS_PAGE_LABEL, main_frame.NEEDS_REVIEW_PAGE_LABEL]


def test_review_notebook_given_the_riders_page_still_holds_the_riders_list() -> None:
    """The Riders page's controls are unchanged by the reorder."""
    assert _control_names(_pages()[0]) == [main_frame.CONSOLE_RIDERS_LIST]


def test_review_notebook_given_the_needs_review_page_still_holds_its_controls() -> None:
    """The Needs Review page keeps flagged_list and review_btn."""
    assert _control_names(_pages()[1]) == ["flagged_list", "review_btn"]


# ------------------------------------------------------- the columns


def test_riders_column_labels_given_the_shared_definitions_cannot_drift() -> None:
    """The console's headers are the shared labels, in order."""
    labels = tuple(column.label for column in CONSOLE_RIDER_COLUMNS)

    assert labels == main_frame.RIDERS_COLUMN_LABELS


def test_riders_column_plate_index_given_the_shared_column_is_zero() -> None:
    """Plate stays column 0, where ``_on_rider_activated`` reads it."""
    assert (main_frame.RIDERS_COL_PLATE, main_frame.RIDERS_COL_NAME) == (0, 1)
    assert main_frame.RIDERS_COL_TEAM == 2
    assert main_frame.RIDERS_COLUMN_LABELS[main_frame.RIDERS_COL_PLATE] == "Plate"


def test_riders_column_sex_and_cards_indexes_given_the_shared_order() -> None:
    """Phase 4: Sex and Cards are the console's own last two columns."""
    labels = main_frame.RIDERS_COLUMN_LABELS

    assert (labels[3], labels[4]) == ("Sex", "Cards")


# --------------------------------------------------- test doubles


class _Column:
    """A ``wx.dataview.DataViewColumn`` double for the native sort."""

    def __init__(self, model_column: int, *, ascending: bool = True) -> None:
        """Carry *model_column* and the header arrow's own direction."""
        self.model_column = model_column
        self.ascending = ascending
        self.sort_orders: list[bool] = []
        self.operations: list[str] = []

    def GetModelColumn(self) -> int:  # noqa: N802 -- wx API name the double mirrors
        """Return the model column this header sorts."""
        return self.model_column

    def IsSortOrderAscending(self) -> bool:  # noqa: N802 -- wx API name the double mirrors
        """Return the header arrow's direction."""
        return self.ascending

    def UnsetAsSortKey(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record the sort-key clear the macOS re-apply needs."""
        self.operations.append("unset")

    def SetSortOrder(self, ascending: bool) -> None:  # noqa: N802, FBT001 -- wx API name
        """Record the direction the view re-applied."""
        self.sort_orders.append(ascending)
        self.operations.append("set")


class _SortEvent:
    """A ``wx.dataview.DataViewEvent`` double for the sorted event."""

    def __init__(self) -> None:
        """Start before the handler ran."""
        self.skipped = False

    def Skip(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record that the handler let the event continue."""
        self.skipped = True


class _ResortModel:
    """A ``RiderRowListModel`` double recording its resorts."""

    def __init__(self) -> None:
        """Start with no resort requested."""
        self.resorts = 0

    def Resort(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record one resort request."""
        self.resorts += 1


class _RidersListControl:
    """A ``wx.dataview.DataViewCtrl`` double for the riders list."""

    def __init__(self, *, sorting: _Column | None = None) -> None:
        """Carry the control's current sorting column, if any."""
        self.sorting = sorting
        self.columns: dict[int, _Column] = {}
        self.model: object | None = None

    def GetSortingColumn(self) -> _Column | None:  # noqa: N802 -- wx API name
        """Return the column the control currently sorts by."""
        return self.sorting

    def GetColumn(self, index: int) -> _Column | None:  # noqa: N802 -- wx API name
        """Return the column at *index*, or ``None``."""
        return self.columns.get(index)

    def AssociateModel(self, model: object) -> None:  # noqa: N802 -- wx API name
        """Record the associated model."""
        self.model = model

    def Refresh(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """No-op repaint (nothing to paint)."""

    def Update(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """No-op repaint (nothing to paint)."""


class _Shell:
    """A ``MainFrame`` double owning only what these methods read.

    The console's ``show_riders``, ``_on_column_sorted`` and
    ``_apply_sort`` are called here as unbound methods against this
    shell, so their wiring is pinned without building a real frame (no
    window, no display).
    """

    def __init__(
        self,
        *,
        control: _RidersListControl | None = None,
        sort_column: int | None = None,
        sort_ascending: bool = True,
    ) -> None:
        """Store the state the three methods under test read."""
        self.console_riders_list = control if control is not None else _RidersListControl()
        self._riders_model: object | None = None
        self._riders_sort_column = sort_column
        self._riders_sort_ascending = sort_ascending

    def _apply_sort(self) -> None:
        """Run the real handler so the show_riders wiring runs."""
        main_frame.MainFrame._apply_sort(self)


# --------------------------------------------------- the page lookup


class _Notebook:
    """A ``wx.Notebook`` stand-in carrying page labels."""

    def __init__(self, *labels: str) -> None:
        """Start with one page per label, in order."""
        self._labels = labels
        self.selection: int | None = None

    def GetPageCount(self) -> int:  # noqa: N802 -- wx API name the stub mirrors
        """Return the number of pages."""
        return len(self._labels)

    def GetPageText(self, index: int) -> str:  # noqa: N802 -- wx API name the stub mirrors
        """Return page *index*'s label."""
        return self._labels[index]

    def SetSelection(self, index: int) -> None:  # noqa: N802 -- wx API name the stub mirrors
        """Record the selected page."""
        self.selection = index


def test_page_index_given_the_needs_review_page_returns_its_index() -> None:
    """The LookupError-free page search follows the XRC's own order."""
    notebook = _Notebook(main_frame.RIDERS_PAGE_LABEL, main_frame.NEEDS_REVIEW_PAGE_LABEL)

    assert main_frame._page_index(notebook, main_frame.NEEDS_REVIEW_PAGE_LABEL) == 1


def test_page_index_given_the_first_page_returns_zero() -> None:
    """T-4 boundary: the first page indexes to 0."""
    notebook = _Notebook(main_frame.RIDERS_PAGE_LABEL, main_frame.NEEDS_REVIEW_PAGE_LABEL)

    assert main_frame._page_index(notebook, main_frame.RIDERS_PAGE_LABEL) == 0


def test_page_index_given_a_reordered_notebook_follows_the_labels() -> None:
    """The lookup is by label, so a reorder cannot break it."""
    notebook = _Notebook("Needs Review", "Riders")

    assert main_frame._page_index(notebook, main_frame.RIDERS_PAGE_LABEL) == 1


def test_page_index_given_a_single_page_returns_zero() -> None:
    """T-4 boundary: one page."""
    notebook = _Notebook("Riders")

    assert main_frame._page_index(notebook, "Riders") == 0


def test_page_index_given_an_absent_label_returns_none() -> None:
    """A notebook without that page reports no index, never raises."""
    notebook = _Notebook("Riders", "Needs Review")

    assert main_frame._page_index(notebook, "Results") is None


def test_page_index_given_no_pages_returns_none() -> None:
    """T-4 boundary: an empty notebook has no page to find."""
    assert main_frame._page_index(_Notebook(), "Riders") is None


# -------------------------------------------------- the native sort


def test_on_column_sorted_given_a_column_remembers_its_state() -> None:
    """A header sort is remembered as its model column + direction."""
    control = _RidersListControl(sorting=_Column(main_frame.RIDERS_COL_NAME, ascending=False))
    shell = _Shell(control=control)
    event = _SortEvent()

    main_frame.MainFrame._on_column_sorted(shell, event)

    assert (shell._riders_sort_column, shell._riders_sort_ascending) == (
        main_frame.RIDERS_COL_NAME,
        False,
    )
    assert event.skipped is True


def test_on_column_sorted_given_no_sorting_column_keeps_the_state() -> None:
    """T-3 negative: wx's "nothing sorted" state changes nothing."""
    shell = _Shell(control=_RidersListControl(sorting=None), sort_column=2, sort_ascending=False)

    main_frame.MainFrame._on_column_sorted(shell, _SortEvent())

    assert (shell._riders_sort_column, shell._riders_sort_ascending) == (2, False)


def test_apply_sort_given_a_remembered_column_restores_it_and_resorts() -> None:
    """The remembered column's arrow and order are re-applied.

    Measured on macOS: ``SetSortOrder`` is a no-op when the direction
    is unchanged, so ``_apply_sort`` must clear the sort key first --
    the recorded order pins unset-then-set.
    """
    column = _Column(main_frame.RIDERS_COL_NAME)
    control = _RidersListControl()
    control.columns[main_frame.RIDERS_COL_NAME] = column
    model = _ResortModel()
    shell = _Shell(control=control, sort_column=main_frame.RIDERS_COL_NAME, sort_ascending=False)
    shell._riders_model = model

    main_frame.MainFrame._apply_sort(shell)

    assert (column.operations, model.resorts) == (["unset", "set"], 1)
    assert column.sort_orders == [False]


def test_apply_sort_given_no_remembered_column_leaves_the_order_alone() -> None:
    """T-3 negative: nothing clicked means the source's own order."""
    column = _Column(main_frame.RIDERS_COL_NAME)
    control = _RidersListControl()
    control.columns[main_frame.RIDERS_COL_NAME] = column
    model = _ResortModel()
    shell = _Shell(control=control)
    shell._riders_model = model

    main_frame.MainFrame._apply_sort(shell)

    assert (column.sort_orders, column.operations, model.resorts) == ([], [], 0)


def test_apply_sort_given_no_model_leaves_the_control_alone() -> None:
    """T-3 negative: rows never rendered means nothing to resort."""
    column = _Column(main_frame.RIDERS_COL_NAME)
    control = _RidersListControl()
    control.columns[main_frame.RIDERS_COL_NAME] = column
    shell = _Shell(control=control, sort_column=main_frame.RIDERS_COL_NAME)

    main_frame.MainFrame._apply_sort(shell)

    assert column.sort_orders == []


def test_apply_sort_given_a_missing_column_leaves_the_model_alone() -> None:
    """T-3 negative: a remembered column the control no longer has."""
    model = _ResortModel()
    shell = _Shell(control=_RidersListControl(), sort_column=main_frame.RIDERS_COL_NAME)
    shell._riders_model = model

    main_frame.MainFrame._apply_sort(shell)

    assert model.resorts == 0


# -------------------------------------------------- show_riders


_ROW = RiderRow(plate="123", name="Sam Ellis", team=None, sex="F", cards=("AS", "KH"))


def test_show_riders_given_rows_builds_the_five_shared_columns() -> None:
    """Phase 4: the console's list carries every shared column."""
    control = _RidersListControl()
    shell = _Shell(control=control)

    main_frame.MainFrame.show_riders(shell, [_ROW])

    assert control.model.GetColumnCount() == len(CONSOLE_RIDER_COLUMNS)
    assert control.model.GetColumnCount() == 5


def test_show_riders_given_a_remembered_sort_re_applies_it() -> None:
    """A rebuilt model drops the sort key; the view restores it."""
    column = _Column(main_frame.RIDERS_COL_NAME)
    control = _RidersListControl()
    control.columns[main_frame.RIDERS_COL_NAME] = column
    shell = _Shell(control=control, sort_column=main_frame.RIDERS_COL_NAME)

    main_frame.MainFrame.show_riders(shell, [_ROW])

    assert column.sort_orders == [True]
    assert control.model.GetCount() == 1


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        (main_frame.RIDERS_COL_PLATE, "123"),
        (main_frame.RIDERS_COL_NAME, "Sam Ellis"),
        (main_frame.RIDERS_COL_TEAM, SOLO_TEAM_TEXT),
        (3, "F"),
        (4, "A♠ K♥"),
    ],
    ids=["plate", "name", "team_solo", "sex", "cards"],
)
def test_show_riders_given_a_row_renders_its_shared_cells(column: int, expected: str) -> None:
    """A solo row renders "solo", its sex, and its card glyphs."""
    control = _RidersListControl()
    shell = _Shell(control=control)

    main_frame.MainFrame.show_riders(shell, [_ROW])

    assert control.model.GetValueByRow(0, column) == expected
