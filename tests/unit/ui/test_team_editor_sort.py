# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the team editor's remembered header sort.

``TeamEditor._apply_sort`` re-applies the operator's header sort after
``show_teams`` rebuilds the model. Clearing the sort key first is the
load-bearing macOS step (``SetSortOrder`` is a no-op when the direction
is unchanged), but the first render has never sorted a column: Windows'
generic ``DataViewColumn.UnsetAsSortKey`` asserts whenever the column is
not the control's sort key and aborts the process there. The clear is
therefore guarded by ``IsSortKey``.

The view method is driven as an unbound method against a shell that
owns only the state it reads, following the rider-editor helpers' own
precedent; the live window needs a real frame and is not pinned here.
"""

from __future__ import annotations

from rivercrossing.ui.views import team_editor


class _Column:
    """A ``wx.dataview.DataViewColumn`` double for the teams sort."""

    def __init__(self, *, is_sort_key: bool = False) -> None:
        """Start as a never-sorted column and record the calls made."""
        self.is_sort_key = is_sort_key
        self.operations: list[str] = []
        self.sort_orders: list[bool] = []

    def IsSortKey(self) -> bool:  # noqa: N802 -- wx API name the double mirrors
        """Return whether the control currently sorts by this column."""
        return self.is_sort_key

    def UnsetAsSortKey(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record the sort-key clear the macOS re-apply needs."""
        self.operations.append("unset")

    def SetSortOrder(self, ascending: bool) -> None:  # noqa: N802, FBT001 -- wx API name
        """Record the direction the view re-applied."""
        self.operations.append("set")
        self.sort_orders.append(ascending)


class _Model:
    """A ``TeamsListModel`` double recording its resorts."""

    def __init__(self) -> None:
        """Start with no resort requested."""
        self.resorts = 0

    def Resort(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record one resort request."""
        self.resorts += 1


class _Control:
    """A ``wx.dataview.DataViewCtrl`` double for ``teams_list``."""

    def __init__(self, columns: dict[int, _Column] | None = None) -> None:
        """Carry the columns ``GetColumn`` can find."""
        self.columns = columns if columns is not None else {}

    def GetColumn(self, index: int) -> _Column | None:  # noqa: N802 -- wx API name
        """Return the column at *index*, or ``None``."""
        return self.columns.get(index)


class _Shell:
    """A ``TeamEditor`` double owning the sort state."""

    def __init__(self, *, control: _Control | None = None) -> None:
        """Store the state the sort method under test reads."""
        self.teams_list = control if control is not None else _Control()
        self._teams_model = _Model()
        self._sort_column = team_editor.COL_NAME
        self._sort_ascending = True

    def _apply_sort(self) -> None:
        """Run the real handler so the show_teams wiring runs."""
        team_editor.TeamEditor._apply_sort(self)


def test_team_editor_apply_sort_given_a_never_sorted_column_leaves_the_sort_key_alone() -> None:
    """First render: skip the clear for a never-sorted column.

    Windows' generic ``UnsetAsSortKey`` asserts there and aborts the
    process, so the unset must not run before the column has sorted.
    """
    column = _Column()
    shell = _Shell(control=_Control({team_editor.COL_NAME: column}))

    team_editor.TeamEditor._apply_sort(shell)

    assert (column.operations, column.sort_orders, shell._teams_model.resorts) == (
        ["set"],
        [True],
        1,
    )


def test_team_editor_apply_sort_given_a_sorted_column_clears_the_sort_key_first() -> None:
    """Measured macOS: clearing a live sort key is load-bearing."""
    column = _Column(is_sort_key=True)
    shell = _Shell(control=_Control({team_editor.COL_NAME: column}))

    team_editor.TeamEditor._apply_sort(shell)

    assert (column.operations, column.sort_orders, shell._teams_model.resorts) == (
        ["unset", "set"],
        [True],
        1,
    )


def test_team_editor_apply_sort_given_a_missing_column_leaves_the_model_alone() -> None:
    """T-3 negative: a remembered column the control no longer has."""
    shell = _Shell()

    team_editor.TeamEditor._apply_sort(shell)

    assert shell._teams_model.resorts == 0
