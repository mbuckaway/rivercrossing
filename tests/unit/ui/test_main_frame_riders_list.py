# SPDX-License-Identifier: GPL-3.0-only
"""Headless Phase-4 pins for the console's riders tab (main_frame).

The console's ``console_riders_list`` draws the shared
``CONSOLE_RIDER_COLUMNS`` (Plate | Name | Team | Sex | Cards) and sorts
on a header click, exactly like the rider editor's own list; the review
notebook opens on that Riders page.

As with ``test_main_frame_ride_header.py``, only what genuinely needs
no window is pinned here:

- the ``review_notebook`` page order, read straight out of
  ``main.xrc`` as XML;
- the three pure helpers the code-side wiring is built from --
  ``_clicked_column_index``, ``_page_index`` and the column-label
  tuple;
- the console's own thin delegates (``set_sort_indicator``,
  ``show_riders``, ``_on_riders_column_header_click``), driven against
  a shell that owns only the state each one reads.

The rest of the wiring -- the ``Bind`` calls that deliver a real header
click or select a real notebook page -- needs a live ``wx`` window and
stays with the functional suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui.presenters.data_source import RiderRow
from rivercrossing.ui.rider_columns import CONSOLE_RIDER_COLUMNS, SOLO_TEAM_TEXT
from rivercrossing.ui.views import main_frame

if TYPE_CHECKING:
    from collections.abc import Sequence

XRC_DIR = Path(__file__).resolve().parents[3] / "src" / "rivercrossing" / "ui" / "xrc"

MAIN_XRC = "main.xrc"


# ------------------------------------------------------------ the XRC


def _review_notebook() -> ET.Element:
    """Return ``main.xrc``'s ``review_notebook`` object."""
    # S314: the project's own XRC, not untrusted input.
    root = ET.parse(XRC_DIR / MAIN_XRC).getroot()  # noqa: S314
    return next(
        obj for obj in root.iter("object") if obj.get("name") == main_frame.REVIEW_NOTEBOOK
    )


def _pages() -> list[ET.Element]:
    """Return the notebook's own ``notebookpage`` children, in order.

    Direct children only, so the list is the page order the loaded
    notebook will have -- page 0 is the default page, which is the
    whole point of this check.
    """
    return [page for page in _review_notebook() if page.get("class") == "notebookpage"]


def _labels() -> list[str]:
    """Return every page's label text, in page order."""
    return [(page.findtext("label") or "") for page in _pages()]


def _control_names(page: ET.Element) -> list[str]:
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
    """A ``wx.dataview.DataViewColumn`` stand-in without wx."""

    def __init__(self, title: str) -> None:
        """Start with *title* as the header text."""
        self.title = title

    def GetTitle(self) -> str:  # noqa: N802 -- wx API name the stub mirrors
        """Return the current header text."""
        return self.title

    def SetTitle(self, title: str) -> None:  # noqa: N802 -- wx API name the stub mirrors
        """Replace the header text."""
        self.title = title


def _columns() -> list[_Column]:
    """Return one stand-in per shared console column, in order."""
    return [_Column(column.label) for column in CONSOLE_RIDER_COLUMNS]


class _HeaderClick:
    """A ``wx.dataview.DataViewEvent`` stand-in for a header click."""

    def __init__(self, column: object) -> None:
        """Carry the column object the operator clicked."""
        self._column = column
        self.skipped = False

    def GetColumn(self) -> object:  # noqa: N802 -- wx API name the stub mirrors
        """Return the clicked column object."""
        return self._column

    def Skip(self) -> None:  # noqa: N802 -- wx API name the stub mirrors
        """Record that the handler let the event continue."""
        self.skipped = True


class _FakePresenter:
    """A ``ConsolePresenter`` stand-in recording forwarded clicks."""

    def __init__(self) -> None:
        """Start with no forwarded click."""
        self.sorted_columns: list[int] = []

    def on_sort_riders(self, column: int) -> None:
        """Record the forwarded column index."""
        self.sorted_columns.append(column)


class _Control:
    """A ``wx.dataview.DataViewCtrl`` stand-in recording its model."""

    def __init__(self) -> None:
        """Start with no associated model."""
        self.model: object | None = None

    def AssociateModel(self, model: object) -> None:  # noqa: N802 -- wx API name
        """Record the associated model."""
        self.model = model

    def Refresh(self) -> None:  # noqa: N802 -- wx API name the stub mirrors
        """No-op repaint (nothing to paint)."""

    def Update(self) -> None:  # noqa: N802 -- wx API name the stub mirrors
        """No-op repaint (nothing to paint)."""


class _Shell:
    """A ``MainFrame`` stand-in owning only what these methods read.

    The console's ``set_sort_indicator``,
    ``_on_riders_column_header_click`` and ``show_riders`` are called
    here as unbound methods against this shell, so their wiring is
    pinned without building a real frame (no window, no display).
    """

    def __init__(
        self,
        *,
        columns: Sequence[_Column] | None = None,
        presenter: _FakePresenter | None = None,
        control: _Control | None = None,
    ) -> None:
        """Store the state the three methods under test read."""
        self._riders_columns = list(columns if columns is not None else _columns())
        self._presenter = presenter
        self.console_riders_list = control
        self._riders_model: object | None = None


# ---------------------------------------------- the clicked-column map


@pytest.mark.parametrize("index", [0, 1, 2, 3, 4], ids=lambda index: f"column_{index}")
def test_clicked_column_index_given_a_shared_column_returns_its_index(index: int) -> None:
    """Every shared column maps back to the sorted index."""
    columns = _columns()

    assert main_frame._clicked_column_index(columns, columns[index]) == index


def test_clicked_column_index_given_an_unknown_object_returns_none() -> None:
    """A click on a column this list never appended sorts nothing."""
    assert main_frame._clicked_column_index(_columns(), _Column("Elsewhere")) is None


def test_clicked_column_index_given_no_columns_returns_none() -> None:
    """T-4 boundary: a list with no columns has nothing to map."""
    assert main_frame._clicked_column_index([], _Column("Plate")) is None


def test_clicked_column_index_given_one_column_returns_zero() -> None:
    """T-4 boundary: the single-column case."""
    column = _Column("Plate")

    assert main_frame._clicked_column_index([column], column) == 0


@given(index=st.integers(min_value=0, max_value=4))
def test_clicked_column_index_given_any_shared_column_round_trips_its_index(index: int) -> None:
    """Property: the column at *index* maps back to *index* (T-7)."""
    columns = _columns()

    assert main_frame._clicked_column_index(columns, columns[index]) == index


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


# -------------------------------------------------- the view wiring


@pytest.mark.parametrize("index", [0, 1, 2, 3, 4], ids=lambda index: f"column_{index}")
def test_on_riders_column_header_click_given_a_column_forwards_its_index(index: int) -> None:
    """Phase 4: a header click reaches ``presenter.on_sort_riders``."""
    columns = _columns()
    presenter = _FakePresenter()
    shell = _Shell(columns=columns, presenter=presenter)

    main_frame.MainFrame._on_riders_column_header_click(shell, _HeaderClick(columns[index]))

    assert presenter.sorted_columns == [index]


def test_on_riders_column_header_click_given_an_unknown_column_forwards_nothing() -> None:
    """T-3 negative: a click this list never appended is ignored."""
    presenter = _FakePresenter()
    shell = _Shell(presenter=presenter)

    main_frame.MainFrame._on_riders_column_header_click(shell, _HeaderClick(_Column("Elsewhere")))

    assert presenter.sorted_columns == []


def test_on_riders_column_header_click_given_no_presenter_skips_the_click() -> None:
    """T-3 negative: an unwired console swallows the click, no crash.

    The guard is what the assertion proves: without it the handler
    would raise on the ``None`` presenter and ``skip`` would never be
    recorded.
    """
    columns = _columns()
    event = _HeaderClick(columns[0])
    shell = _Shell(columns=columns, presenter=None)

    main_frame.MainFrame._on_riders_column_header_click(shell, event)

    assert event.skipped is True


def test_on_riders_column_header_click_given_no_presenter_and_no_column_skips() -> None:
    """T-13 row 4: both guards together still let the event continue."""
    columns = _columns()
    event = _HeaderClick(_Column("Elsewhere"))
    shell = _Shell(columns=columns, presenter=None)

    main_frame.MainFrame._on_riders_column_header_click(shell, event)

    assert event.skipped is True


def test_on_riders_column_header_click_given_a_click_skips_the_event() -> None:
    """The handler lets wx continue, like the editor's own."""
    columns = _columns()
    event = _HeaderClick(columns[2])
    shell = _Shell(columns=columns, presenter=_FakePresenter())

    main_frame.MainFrame._on_riders_column_header_click(shell, event)

    assert event.skipped is True


def test_set_sort_indicator_given_a_column_marks_its_header() -> None:
    """Phase 4: the console's ▲/▼ marker is the shared indicator's."""
    shell = _Shell()

    main_frame.MainFrame.set_sort_indicator(shell, 1, ascending=True)

    assert [column.GetTitle() for column in shell._riders_columns] == [
        "Plate",
        "Name ▲",
        "Team",
        "Sex",
        "Cards",
    ]


def test_set_sort_indicator_given_no_column_clears_every_header() -> None:
    """No active sort restores every plain label (T-3 False branch)."""
    shell = _Shell()
    main_frame.MainFrame.set_sort_indicator(shell, 1, ascending=True)

    main_frame.MainFrame.set_sort_indicator(shell, None, ascending=True)

    assert [column.GetTitle() for column in shell._riders_columns] == [
        "Plate",
        "Name",
        "Team",
        "Sex",
        "Cards",
    ]


# -------------------------------------------------- show_riders


_ROW = RiderRow(plate="123", name="Sam Ellis", team=None, sex="F", cards=("AS", "KH"))


def test_show_riders_given_rows_builds_the_five_shared_columns() -> None:
    """Phase 4: the console's list carries every shared column."""
    control = _Control()
    shell = _Shell(control=control)

    main_frame.MainFrame.show_riders(shell, [_ROW])

    assert control.model.GetColumnCount() == len(CONSOLE_RIDER_COLUMNS)
    assert control.model.GetColumnCount() == 5


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        (main_frame.RIDERS_COL_PLATE, "123"),
        (main_frame.RIDERS_COL_NAME, "Sam Ellis"),
        (main_frame.RIDERS_COL_TEAM, SOLO_TEAM_TEXT),
        (3, "F"),
        (4, "AS KH"),
    ],
    ids=["plate", "name", "team_solo", "sex", "cards"],
)
def test_show_riders_given_a_row_renders_its_shared_cells(column: int, expected: str) -> None:
    """A solo row renders "solo", its sex, and its card codes."""
    control = _Control()
    shell = _Shell(control=control)

    main_frame.MainFrame.show_riders(shell, [_ROW])

    assert control.model.GetValueByRow(0, column) == expected
