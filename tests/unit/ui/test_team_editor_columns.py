# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the team editor's two list columns.

``team_editor_dlg``'s ``teams_list`` is a two-column
``wxDataViewCtrl`` ("Team | Riders") built by
``TeamEditor._build_team_columns``. Two measured wx behaviours made
those columns awkward and are pinned here as plain module data -- no
window, no ``wx.App``:

- ``AppendTextColumn``'s default flags already include
  ``wxDATAVIEW_COL_RESIZABLE``, but an explicit ``flags=`` argument
  *replaces* that default rather than OR-ing into it, so the pre-fix
  ``flags=wxDATAVIEW_COL_SORTABLE`` dropped resizability (macOS then
  set the column ``NSTableColumnNoResizing``). The combined constant
  must carry both bits.
- The default width resolves to ``wxDVC_DEFAULT_WIDTH`` (80 DIP);
  the first ("Team") column now opens at double that.

The columns' live layout stays with the (disabled) functional suite,
``tests/functional/test_team_editor.py``.
"""

import wx

from rivercrossing.ui.views import team_editor


def test_team_editor_columns_flags_keep_the_teams_column_resizable() -> None:
    """The explicit flags retain the resizable bit wx drops."""
    flags = team_editor.TEAMS_LIST_COLUMN_FLAGS

    assert (flags & wx.dataview.DATAVIEW_COL_RESIZABLE) == wx.dataview.DATAVIEW_COL_RESIZABLE


def test_team_editor_columns_flags_keep_the_teams_column_sortable() -> None:
    """Sorting is why the old ``flags=`` existed; keep it."""
    flags = team_editor.TEAMS_LIST_COLUMN_FLAGS

    assert (flags & wx.dataview.DATAVIEW_COL_SORTABLE) == wx.dataview.DATAVIEW_COL_SORTABLE


def test_team_editor_columns_name_column_width_is_double_the_80_dip_default() -> None:
    """The first ("Team") column opens twice wx's 80 DIP default."""
    assert team_editor.COL_NAME_WIDTH == 160


def test_team_editor_columns_riders_column_width_keeps_the_80_dip_default() -> None:
    """The second ("Riders") column keeps the platform default width."""
    assert team_editor.COL_RIDERS_WIDTH == 80
