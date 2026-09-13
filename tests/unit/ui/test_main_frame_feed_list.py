# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the console's crossings feed (Phase 4).

Four behaviours, none of which needs a display:

- the **search row** ``main.xrc`` now declares above ``crossings_list``
  (a ``wxStaticText`` label + ``crossings_search``), read as XML;
- the **column flags** every feed column is appended with (sortable and
  resizable) and the two hide-times columns ``_build_columns`` returns;
- the **native header sort** -- the default Time-ascending arrow, the
  remembered column re-applied after every ``show_feed`` rebuild, and
  ``CrossingsFeedModel.Compare``'s own per-column keys;
- the **search forwarding** the view's ``_on_search_text`` handler does.

The view methods are driven as unbound methods against a shell that owns
only the state each one reads (``test_main_frame_riders_list.py``'s
precedent), while ``Compare``/``GetValueByRow`` run on the real model --
constructing one needs no ``wx.App``. The ``Bind`` calls that deliver a
real keystroke or header click need a live control and are not pinned
here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import wx
from defusedxml.ElementTree import parse

from rivercrossing.ui import feed_model
from rivercrossing.ui.presenters.data_source import FeedRow
from rivercrossing.ui.views import main_frame

if TYPE_CHECKING:
    # Type-only: defusedxml does not re-export the Element class.
    from xml.etree.ElementTree import Element

XRC_DIR = Path(__file__).resolve().parents[3] / "src" / "rivercrossing" / "ui" / "xrc"

MAIN_XRC = "main.xrc"


# --------------------------------------------------------- the doubles


class _Column:
    """A ``wx.dataview.DataViewColumn`` double for the native sort."""

    def __init__(
        self, model_column: int, *, ascending: bool = True, is_sort_key: bool = False
    ) -> None:
        """Carry *model_column*, the arrow and the sort-key state.

        *is_sort_key* defaults to ``False``: a freshly built column has
        never sorted the control, which is exactly the state Windows'
        generic ``DataView`` aborts on when the key is cleared.
        """
        self.model_column = model_column
        self.ascending = ascending
        self.is_sort_key = is_sort_key
        self.sort_orders: list[bool] = []
        self.operations: list[str] = []

    def GetModelColumn(self) -> int:  # noqa: N802 -- wx API name the double mirrors
        """Return the model column this header sorts."""
        return self.model_column

    def IsSortOrderAscending(self) -> bool:  # noqa: N802 -- wx API name the double mirrors
        """Return the header arrow's direction."""
        return self.ascending

    def IsSortKey(self) -> bool:  # noqa: N802 -- wx API name the double mirrors
        """Return whether the control currently sorts by this column."""
        return self.is_sort_key

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
    """A ``DataViewIndexListModel`` double recording its resorts."""

    def __init__(self) -> None:
        """Start with no resort requested."""
        self.resorts = 0

    def Resort(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record one resort request."""
        self.resorts += 1


class _CrossingsListControl:
    """A ``wx.dataview.DataViewCtrl`` double for the crossings list."""

    def __init__(self, *, sorting: _Column | None = None) -> None:
        """Carry the control's current sorting column, if any."""
        self.sorting = sorting
        self.columns: dict[int, _Column] = {}
        self.model: Any = None  # whatever AssociateModel was handed
        self.appended: list[tuple[str, int, int, int]] = []

    def GetSortingColumn(self) -> _Column | None:  # noqa: N802 -- wx API name
        """Return the column the control currently sorts by."""
        return self.sorting

    def GetColumn(self, index: int) -> _Column | None:  # noqa: N802 -- wx API name
        """Return the column at *index*, or ``None``."""
        return self.columns.get(index)

    def AssociateModel(self, model: object) -> None:  # noqa: N802 -- wx API name
        """Record the associated model."""
        self.model = model

    def AppendTextColumn(  # noqa: N802, PLR0913 -- wx API name; its own four fields
        self, label: str, model_col: int, *, width: int, flags: int
    ) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Record one appended column and return its double."""
        self.appended.append((label, model_col, width, flags))
        column = _Column(model_col)
        self.columns[model_col] = column
        return column


class _SearchCtrl:
    """A ``wx.SearchCtrl`` double carrying its own text."""

    def __init__(self, value: str = "") -> None:
        """Start holding *value*."""
        self._value = value

    def GetValue(self) -> str:  # noqa: N802 -- wx API name the double mirrors
        """Return the box's current text."""
        return self._value


class _Presenter:
    """A presenter double recording the forwarded search text."""

    def __init__(self) -> None:
        """Start with no forwarded text."""
        self.search_texts: list[str] = []

    def on_search_text(self, text: str) -> None:
        """Record one forwarded search-box value."""
        self.search_texts.append(text)


class _Shell:
    """A ``MainFrame`` double owning only what these methods read."""

    def __init__(  # noqa: PLR0913 -- (control, search) + the two sort fields
        self,
        *,
        control: _CrossingsListControl | None = None,
        search: _SearchCtrl | None = None,
        sort_column: int = feed_model.COL_TIME,
        sort_ascending: bool = True,
    ) -> None:
        """Store the state the methods under test read."""
        self.crossings_list = control if control is not None else _CrossingsListControl()
        self.crossings_search = search if search is not None else _SearchCtrl()
        self._crossings_model: Any = None  # the model show_feed just built
        self._feed_sort_column = sort_column
        self._feed_sort_ascending = sort_ascending
        self._presenter: _Presenter | None = None

    def _notify_ride_changed(self) -> None:
        """Discard the menu-binder notification the real frame fires."""

    def _apply_feed_sort(self) -> None:
        """Run the real handler so the show_feed wiring runs."""
        main_frame.MainFrame._apply_feed_sort(self)


# ------------------------------------------------------------ the XRC


def _main_frame_objects() -> list[Element]:
    """Return every named ``<object>`` in ``main.xrc``."""
    root = parse(XRC_DIR / MAIN_XRC).getroot()
    return [obj for obj in root.iter("object") if obj.get("name")]


def _object_named(name: str) -> Element:
    """Return the named ``<object>`` in ``main.xrc``."""
    return next(obj for obj in _main_frame_objects() if obj.get("name") == name)


def _sizer_holding(name: str) -> Element:
    """Return the innermost sizer holding the named control."""
    root = parse(XRC_DIR / MAIN_XRC).getroot()
    holding = [
        obj
        for obj in root.iter("object")
        if obj.attrib.get("class", "").endswith("Sizer")
        and any(child.get("name") == name for child in obj.iter("object"))
    ]
    return holding[-1]


def _named_children(sizer: Element) -> list[str]:
    """List every named control below *sizer*, in document order."""
    return [obj.get("name") or "" for obj in sizer.iter("object") if obj.get("name")]


def _sizeritem_of(name: str) -> Element:
    """Return the sizer item whose own object is the named control."""
    return next(
        obj
        for obj in parse(XRC_DIR / MAIN_XRC).getroot().iter("object")
        if obj.attrib.get("class") == "sizeritem"
        and obj.find("object") is not None
        and obj.find("object").get("name") == name
    )


def test_crossings_search_is_declared_as_a_search_ctrl() -> None:
    """A native ``wxSearchCtrl``, the rider editor's class."""
    assert _object_named("crossings_search").attrib["class"] == "wxSearchCtrl"


def test_crossings_search_row_carries_the_search_label() -> None:
    """UX-DESKTOP §7: the box carries a real, persistent label."""
    labels = [
        obj.findtext("label") or ""
        for obj in _sizer_holding("crossings_search").iter("object")
        if obj.attrib.get("class") == "wxStaticText"
    ]

    assert labels == ["Search"]


def test_crossings_search_sits_above_the_crossings_list_in_the_left_pane() -> None:
    """The search row leads the list in the pane's sizer."""
    assert _named_children(_sizer_holding("crossings_list")) == [
        "crossings_search",
        "crossings_list",
    ]


def test_crossings_list_still_takes_the_panes_own_slack() -> None:
    """The list keeps ``option=1|wxEXPAND``; the row does not."""
    item = _sizeritem_of("crossings_list")

    assert (item.findtext("option"), item.findtext("flag")) == ("1", "wxEXPAND")


# ------------------------------------------------------- the columns


def test_feed_column_flags_include_the_sortable_and_resizable_bits() -> None:
    """Both bits are spelled out: flags= replaces defaults."""
    flags = main_frame.FEED_COLUMN_FLAGS

    assert (
        flags & wx.dataview.DATAVIEW_COL_SORTABLE,
        flags & wx.dataview.DATAVIEW_COL_RESIZABLE,
    ) == (wx.dataview.DATAVIEW_COL_SORTABLE, wx.dataview.DATAVIEW_COL_RESIZABLE)


def test_build_columns_given_the_shell_appends_every_feed_column_with_flags() -> None:
    """Seven columns, canvas order, pinned widths and both flags."""
    control = _CrossingsListControl()
    shell = _Shell(control=control)

    main_frame.MainFrame._build_columns(shell)

    assert [(label, col) for label, col, _width, _flags in control.appended] == list(
        zip(feed_model.COLUMN_LABELS, range(len(feed_model.COLUMN_LABELS)), strict=True)
    )
    assert [width for _label, _col, width, _flags in control.appended] == list(
        feed_model.COLUMN_WIDTHS
    )
    assert {flags for _label, _col, _width, flags in control.appended} == {
        main_frame.FEED_COLUMN_FLAGS
    }


def test_build_columns_given_the_shell_returns_only_the_hide_times_columns() -> None:
    """R-37's toggle keeps its handle on Lap time and Total alone."""
    control = _CrossingsListControl()
    shell = _Shell(control=control)

    hideable = main_frame.MainFrame._build_columns(shell)

    assert hideable == (
        control.columns[feed_model.COL_LAP_TIME],
        control.columns[feed_model.COL_TOTAL],
    )


# ----------------------------------------------------- the default sort


def test_default_feed_sort_is_the_time_column_ascending() -> None:
    """Oldest crossing first: elapsed 0 at the top (Phase 4)."""
    assert main_frame.DEFAULT_FEED_SORT == (feed_model.COL_TIME, True)


def test_show_feed_given_rows_applies_the_default_sort_after_the_rebuild() -> None:
    """A fresh model drops the key; the view puts the arrow back.

    The first render has never sorted this column, so it is not a sort
    key: the view sets the arrow without first clearing it -- the clear
    is what Windows' generic ``DataView`` aborts on.
    """
    column = _Column(feed_model.COL_TIME)
    control = _CrossingsListControl()
    control.columns[feed_model.COL_TIME] = column
    shell = _Shell(control=control)

    main_frame.MainFrame.show_feed(shell, [_feed_row()])

    assert (column.operations, column.sort_orders) == (["set"], [True])


def test_show_feed_given_a_remembered_column_re_applies_that_column() -> None:
    """An operator's header click survives the next rebuild."""
    column = _Column(feed_model.COL_NAME)
    control = _CrossingsListControl()
    control.columns[feed_model.COL_NAME] = column
    shell = _Shell(control=control, sort_column=feed_model.COL_NAME, sort_ascending=False)

    main_frame.MainFrame.show_feed(shell, [_feed_row()])

    assert column.sort_orders == [False]


def test_show_feed_given_rows_associates_a_model_carrying_them_all() -> None:
    """The model carries the presenter's own row set, in order."""
    control = _CrossingsListControl()
    shell = _Shell(control=control)

    main_frame.MainFrame.show_feed(shell, [_feed_row(plate="12"), _feed_row(plate="34")])

    assert control.model.GetCount() == 2
    assert control.model.GetValueByRow(0, feed_model.COL_PLATE) == "12"


def test_on_feed_column_sorted_given_a_column_remembers_its_state() -> None:
    """A header sort is remembered as its model column + direction."""
    control = _CrossingsListControl(sorting=_Column(feed_model.COL_LAP, ascending=False))
    shell = _Shell(control=control)
    event = _SortEvent()

    main_frame.MainFrame._on_feed_column_sorted(shell, event)

    assert (shell._feed_sort_column, shell._feed_sort_ascending) == (feed_model.COL_LAP, False)
    assert event.skipped is True


def test_on_feed_column_sorted_given_no_sorting_column_keeps_the_state() -> None:
    """T-3 negative: wx's "nothing sorted" state changes nothing."""
    shell = _Shell(
        control=_CrossingsListControl(sorting=None),
        sort_column=feed_model.COL_LAP,
        sort_ascending=False,
    )

    main_frame.MainFrame._on_feed_column_sorted(shell, _SortEvent())

    assert (shell._feed_sort_column, shell._feed_sort_ascending) == (feed_model.COL_LAP, False)


def test_apply_feed_sort_given_no_model_leaves_the_control_alone() -> None:
    """T-3 negative: rows never rendered means nothing to resort."""
    column = _Column(feed_model.COL_TIME)
    control = _CrossingsListControl()
    control.columns[feed_model.COL_TIME] = column
    shell = _Shell(control=control)

    main_frame.MainFrame._apply_feed_sort(shell)

    assert column.sort_orders == []


def test_apply_feed_sort_given_a_missing_column_leaves_the_model_alone() -> None:
    """T-3 negative: a remembered column the control no longer has."""
    model = _ResortModel()
    shell = _Shell(control=_CrossingsListControl())
    shell._crossings_model = model

    main_frame.MainFrame._apply_feed_sort(shell)

    assert model.resorts == 0


def test_apply_feed_sort_given_a_never_sorted_column_leaves_the_sort_key_alone() -> None:
    """First render: skip the clear for a never-sorted column.

    Windows' generic ``DataViewColumn.UnsetAsSortKey`` asserts ("column
    is not used for sorting") whenever the column is not the control's
    sort key -- the first render's exact state -- and aborts the
    process; skipping the clear keeps it alive.
    """
    column = _Column(feed_model.COL_TIME)
    control = _CrossingsListControl()
    control.columns[feed_model.COL_TIME] = column
    model = _ResortModel()
    shell = _Shell(control=control)
    shell._crossings_model = model

    main_frame.MainFrame._apply_feed_sort(shell)

    assert (column.operations, column.sort_orders, model.resorts) == (["set"], [True], 1)


def test_apply_feed_sort_given_a_remembered_column_unset_then_sets_then_resorts() -> None:
    """Measured macOS: clearing a live sort key is load-bearing."""
    column = _Column(feed_model.COL_TOTAL, is_sort_key=True)
    control = _CrossingsListControl()
    control.columns[feed_model.COL_TOTAL] = column
    model = _ResortModel()
    shell = _Shell(control=control, sort_column=feed_model.COL_TOTAL, sort_ascending=False)
    shell._crossings_model = model

    main_frame.MainFrame._apply_feed_sort(shell)

    assert (column.operations, column.sort_orders, model.resorts) == (["unset", "set"], [False], 1)


# --------------------------------------------------------- the search


def test_on_search_text_given_a_presenter_forwards_the_controls_text() -> None:
    """Every event path reads the box's current value."""
    shell = _Shell(search=_SearchCtrl("12"))
    shell._presenter = _Presenter()
    event = _SortEvent()

    main_frame.MainFrame._on_search_text(shell, event)

    assert shell._presenter.search_texts == ["12"]
    assert event.skipped is True


def test_on_search_text_given_a_cleared_box_forwards_the_empty_text() -> None:
    """T-4 boundary: the clear X forwards "" and restores all."""
    shell = _Shell(search=_SearchCtrl(""))
    shell._presenter = _Presenter()

    main_frame.MainFrame._on_search_text(shell, _SortEvent())

    assert shell._presenter.search_texts == [""]


def test_on_search_text_given_no_presenter_still_lets_the_event_continue() -> None:
    """T-3 negative: a console with no ride has nothing to filter."""
    event = _SortEvent()

    main_frame.MainFrame._on_search_text(_Shell(search=_SearchCtrl("12")), event)

    assert event.skipped is True


# ------------------------------------------- CrossingsFeedModel.Compare


def _compare(  # noqa: PLR0913, PLR0917 -- (model, first, second, column) + the arrow
    model: main_frame.CrossingsFeedModel,
    first: int,
    second: int,
    column: int,
    *,
    ascending: bool,
) -> int:
    """Return ``Compare``'s Ordering for two model rows on *column*.

    wx hands ``Compare`` two items plus the arrow's direction; this
    resolves the items from row indexes so a test reads as two rows.
    """
    return model.Compare(model.GetItem(first), model.GetItem(second), column, ascending)


def test_compare_given_the_time_column_orders_by_elapsed_seconds() -> None:
    """Ascending puts the earliest reading first; descending flips."""
    model = _model(_feed_row(elapsed_s=600.0), _feed_row(elapsed_s=10.0))

    ascending = _compare(model, 0, 1, feed_model.COL_TIME, ascending=True)
    descending = _compare(model, 0, 1, feed_model.COL_TIME, ascending=False)

    assert (ascending, descending) == (1, -1)


def test_compare_given_the_plate_column_orders_numerically() -> None:
    """Plate 2 versus plate 10: the rider lists' numeric-aware rule."""
    model = _model(_feed_row(plate="10"), _feed_row(plate="2"))

    result = _compare(model, 0, 1, feed_model.COL_PLATE, ascending=True)

    assert result == 1


def test_compare_given_the_name_column_orders_case_insensitively() -> None:
    """Casefolded names: "amy" sorts with "Amy", before "Zoe"."""
    model = _model(_feed_row(entry="Zoe"), _feed_row(entry="amy"))

    result = _compare(model, 0, 1, feed_model.COL_NAME, ascending=True)

    assert result == 1


def test_compare_given_the_lap_column_orders_numerically() -> None:
    """Lap 2 before lap 10, never the "10" < "2" text order."""
    model = _model(_feed_row(lap=10), _feed_row(lap=2))

    result = _compare(model, 0, 1, feed_model.COL_LAP, ascending=True)

    assert result == 1


def test_compare_given_equal_keys_falls_back_to_the_rows_own_order() -> None:
    """Equal keys keep the rendered order under either arrow."""
    model = _model(_feed_row(elapsed_s=600.0), _feed_row(elapsed_s=600.0))

    ascending = _compare(model, 0, 1, feed_model.COL_TIME, ascending=True)
    descending = _compare(model, 0, 1, feed_model.COL_TIME, ascending=False)

    assert (ascending, descending) == (-1, -1)


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        (feed_model.COL_TIME, -1),
        (feed_model.COL_PLATE, -1),
        (feed_model.COL_NAME, -1),
        (feed_model.COL_CARD, -1),
        (feed_model.COL_LAP, -1),
        (feed_model.COL_LAP_TIME, -1),
        (feed_model.COL_TOTAL, -1),
    ],
    ids=["time", "plate", "name", "card", "lap", "lap_time", "total"],
)
def test_compare_given_each_column_orders_by_that_columns_own_key(
    column: int, expected: int
) -> None:
    """Every column has a key; row 0's values are the smaller."""
    model = _model(
        _feed_row(
            plate="2", entry="Amy", lap=2, card="9H", elapsed_s=10.0, lap_time_s=10.0, total_s=10.0
        ),
        _feed_row(
            plate="10",
            entry="Zoe",
            lap=10,
            card="KH",
            elapsed_s=600.0,
            lap_time_s=600.0,
            total_s=600.0,
        ),
    )

    result = _compare(model, 0, 1, column, ascending=True)

    assert result == expected


def test_get_value_by_row_given_a_dnf_row_appends_the_marker_to_the_name() -> None:
    """Phase 4: the DNF marker renders on the Name cell."""
    model = _model(_feed_row(entry="Rider 12", dnf=True))

    assert model.GetValueByRow(0, feed_model.COL_NAME) == "Rider 12 DNF"


def test_get_value_by_row_given_a_healthy_row_leaves_the_name_alone() -> None:
    """T-3 negative: nothing marked, no suffix."""
    model = _model(_feed_row(entry="Rider 12"))

    assert model.GetValueByRow(0, feed_model.COL_NAME) == "Rider 12"


def test_get_value_by_row_given_a_missed_row_renders_the_blank_lap() -> None:
    """K's miss keeps its blank numeric cells (unchanged by Phase 4)."""
    model = _model(_feed_row(plate="-", entry="missed", lap=0, missed=True))

    assert (
        model.GetValueByRow(0, feed_model.COL_LAP),
        model.GetValueByRow(0, feed_model.COL_NAME),
    ) == ("", "missed")


def _feed_row(  # noqa: PLR0913 -- one keyword per feed field a test varies
    *,
    plate: str = "1",
    entry: str = "Rider",
    lap: int = 1,
    card: str = "9H",
    dnf: bool = False,
    missed: bool = False,
    elapsed_s: float = 0.0,
    lap_time_s: float = 0.0,
    total_s: float = 0.0,
) -> FeedRow:
    """Build a minimal ``FeedRow`` varying only what a test needs."""
    return FeedRow(
        time="0:10:00",
        plate=plate,
        entry=entry,
        lap=lap,
        lap_time="1:00",
        total="1:00",
        card=card,
        dnf=dnf,
        missed=missed,
        elapsed_s=elapsed_s,
        lap_time_s=lap_time_s,
        total_s=total_s,
    )


def _model(*rows: FeedRow) -> main_frame.CrossingsFeedModel:
    """Build the real feed model over *rows*, in the given order."""
    return main_frame.CrossingsFeedModel(list(rows))
