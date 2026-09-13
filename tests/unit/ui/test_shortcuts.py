# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for shortcuts_dlg's columns and floor (Phase 6).

``shortcuts_dlg`` opened at whatever a plain ``Fit()`` measured,
with two unpinned ``DataViewCtrl`` columns at the platform
default, so the Action text clipped on first open.
``ShortcutsDialog`` now appends its two columns at
:data:`SHORTCUT_COLUMN_WIDTHS` and floors the dialog at
:data:`SHORTCUTS_MIN_SIZE`.

A real ``wx.Dialog`` needs a desktop, so these tests drive a
recording window double and call the steps directly -- the same
``object.__new__`` stand-in ``test_team_editor_dialog_size.py``
uses for its own sizing step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from rivercrossing.ui.views import shortcuts
from rivercrossing.ui.views.shortcuts import (
    COL_ACTION,
    COL_KEY,
    SHORTCUT_COLUMN_LABELS,
    SHORTCUT_COLUMN_WIDTHS,
    SHORTCUTS_MIN_SIZE,
    ShortcutsDialog,
)

if TYPE_CHECKING:
    import wx


class _RecordingColumn:
    """The column object ``AppendTextColumn`` returns."""


class _ShortcutsList:
    """A ``DataViewCtrl`` double recording appended columns."""

    def __init__(self) -> None:
        """Start with no column appended."""
        self.appended: list[tuple[str, int, int]] = []

    def AppendTextColumn(  # noqa: N802 -- wx API name the SUT calls
        self, label: str, model_col: int, *, width: int
    ) -> _RecordingColumn:
        """Record one appended column and return its double."""
        self.appended.append((label, model_col, width))
        return _RecordingColumn()


class _SizingDialog:
    """A dialog double recording every sizing call the SUT makes."""

    def __init__(self) -> None:
        """Start with no sizing call recorded."""
        self.calls: list[str] = []
        self.min_size: wx.Size | None = None

    def SetMinSize(self, size: wx.Size) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the floor the SUT applied."""
        self.calls.append("SetMinSize")
        self.min_size = size

    def Fit(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the fitting call."""
        self.calls.append("Fit")


def _bare_view(
    *,
    dialog: _SizingDialog | None = None,
    control: _ShortcutsList | None = None,
) -> ShortcutsDialog:
    """Return a ``ShortcutsDialog`` over doubles, no window.

    ``__init__`` resolves ``shortcuts_list`` through
    ``FindWindowByName`` and associates a model, which needs a
    desktop; the column and sizing steps below read only
    ``self.dialog`` and ``self.shortcuts_list``, so the instance is
    made without it.
    """
    view = object.__new__(ShortcutsDialog)
    view.dialog = _SizingDialog() if dialog is None else dialog
    view.shortcuts_list = _ShortcutsList() if control is None else control
    return view


# -------------------------------------------------- pinned defaults


def test_shortcuts_column_widths_are_120_then_320() -> None:
    """Phase 6: Key opens 120 wide, Action 320."""
    assert SHORTCUT_COLUMN_WIDTHS == (120, 320)


def test_shortcuts_min_size_is_the_480x300_floor() -> None:
    """Phase 6: the dialog opens no smaller than 480x300."""
    assert SHORTCUTS_MIN_SIZE == (480, 300)


@pytest.mark.parametrize("name", ["SHORTCUTS_MIN_SIZE", "SHORTCUT_COLUMN_WIDTHS"])
def test_shortcuts_public_api_exports_the_sizing_constants(name: str) -> None:
    """Phase 6: both sizing constants are in the public API."""
    assert name in shortcuts.__all__


# ------------------------------------------------------ the columns


def test_shortcuts_build_columns_given_the_shell_appends_both_pinned_widths() -> None:
    """Phase 6: both columns take their own pinned width."""
    control = _ShortcutsList()

    _bare_view(control=control)._build_columns()

    assert control.appended == [
        (SHORTCUT_COLUMN_LABELS[COL_KEY], COL_KEY, SHORTCUT_COLUMN_WIDTHS[COL_KEY]),
        (SHORTCUT_COLUMN_LABELS[COL_ACTION], COL_ACTION, SHORTCUT_COLUMN_WIDTHS[COL_ACTION]),
    ]


# --------------------------------------------------------- the floor


def test_shortcuts_apply_min_size_given_a_small_dialog_floors_it_at_the_minimum() -> None:
    """Phase 6: the dialog's floor is SHORTCUTS_MIN_SIZE."""
    dialog = _SizingDialog()

    _bare_view(dialog=dialog)._apply_min_size()

    assert (dialog.min_size.width, dialog.min_size.height) == SHORTCUTS_MIN_SIZE


def test_shortcuts_apply_min_size_given_a_small_dialog_fits_then_refloors() -> None:
    """Phase 6: SetMinSize, then Fit, then re-floor."""
    dialog = _SizingDialog()

    _bare_view(dialog=dialog)._apply_min_size()

    assert dialog.calls == ["SetMinSize", "Fit", "SetMinSize"]
