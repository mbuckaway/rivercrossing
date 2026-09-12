# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for both rider lists' column flags and widths.

``rider_editor_dlg``'s ``riders_list`` and the console's
``console_riders_list`` now adopt the team editor's / ride library's
native sort pattern. Two measured wx behaviours make that awkward and
are pinned here as plain module data -- no window, no ``wx.App``
(``test_team_editor_columns.py``'s own shape):

- ``AppendTextColumn``'s default flags already include
  ``wxDATAVIEW_COL_RESIZABLE``, but an explicit ``flags=`` argument
  *replaces* that default rather than OR-ing into it, so the combined
  constant must carry the sortable bit *and* keep resizability.
- Every column defaults to ``wxDVC_DEFAULT_WIDTH`` (80 DIP); the Name
  column carries the rider the operator reads, so it opens at double
  that.

The columns' live layout stays with the (disabled) functional suite.
"""

import pytest
import wx

from rivercrossing.ui.views import main_frame, rider_editor

# ------------------------------------------------------- rider editor


def test_rider_editor_column_flags_given_the_shared_list_keep_it_resizable() -> None:
    """The explicit flags retain the resizable bit wx drops."""
    flags = rider_editor.RIDERS_LIST_COLUMN_FLAGS

    assert (flags & wx.dataview.DATAVIEW_COL_RESIZABLE) == wx.dataview.DATAVIEW_COL_RESIZABLE


def test_rider_editor_column_flags_given_the_shared_list_keep_it_sortable() -> None:
    """Native header sorting is what the flags are for."""
    flags = rider_editor.RIDERS_LIST_COLUMN_FLAGS

    assert (flags & wx.dataview.DATAVIEW_COL_SORTABLE) == wx.dataview.DATAVIEW_COL_SORTABLE


def test_rider_editor_name_column_width_given_the_platform_default_is_double() -> None:
    """The Name column opens twice wx's 80 DIP default."""
    assert rider_editor.COL_NAME_WIDTH == 160
    assert rider_editor.COLUMN_WIDTHS[rider_editor.COL_NAME] == rider_editor.COL_NAME_WIDTH


@pytest.mark.parametrize(
    "column", [rider_editor.COL_PLATE, rider_editor.COL_TEAM, rider_editor.COL_SEX]
)
def test_rider_editor_other_columns_given_the_platform_default_keep_it(column: int) -> None:
    """Plate/Team/Sex keep the 80 DIP default width."""
    assert rider_editor.COLUMN_WIDTHS[column] == rider_editor.COL_DEFAULT_WIDTH


def test_rider_editor_column_widths_given_the_shared_labels_are_one_each() -> None:
    """One width per shared EDITOR_RIDER_COLUMNS entry."""
    assert len(rider_editor.COLUMN_WIDTHS) == len(rider_editor.COLUMN_LABELS)


# ------------------------------------------------------------- console


def test_main_frame_riders_column_flags_given_the_shared_list_keep_it_resizable() -> None:
    """The console's list keeps the resizable bit too."""
    flags = main_frame.RIDERS_LIST_COLUMN_FLAGS

    assert (flags & wx.dataview.DATAVIEW_COL_RESIZABLE) == wx.dataview.DATAVIEW_COL_RESIZABLE


def test_main_frame_riders_column_flags_given_the_shared_list_keep_it_sortable() -> None:
    """The console's list is natively sortable like the editor's."""
    flags = main_frame.RIDERS_LIST_COLUMN_FLAGS

    assert (flags & wx.dataview.DATAVIEW_COL_SORTABLE) == wx.dataview.DATAVIEW_COL_SORTABLE


def test_main_frame_riders_name_column_width_given_the_platform_default_is_double() -> None:
    """The console's Name column matches the editor's 160 DIP width."""
    assert main_frame.RIDERS_COLUMN_WIDTHS[main_frame.RIDERS_COL_NAME] == 160


def test_main_frame_riders_column_widths_given_the_shared_labels_are_one_each() -> None:
    """One width per shared CONSOLE_RIDER_COLUMNS entry."""
    assert len(main_frame.RIDERS_COLUMN_WIDTHS) == len(main_frame.RIDERS_COLUMN_LABELS)
