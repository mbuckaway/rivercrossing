# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for shortcuts_dlg's columns, floor and key spelling.

``shortcuts_dlg`` opened at whatever a plain ``Fit()`` measured,
with two unpinned ``DataViewCtrl`` columns at the platform
default, so the Action text clipped on first open.
``ShortcutsDialog`` now appends its two columns at
:data:`SHORTCUT_COLUMN_WIDTHS` and floors the dialog at
:data:`SHORTCUTS_MIN_SIZE`.

The dialog also respells ``Ctrl`` as ``Cmd`` in the Key column on
macOS (spec §15: "Ctrl ⇒ ⌘"), display only -- the accelerators
themselves and :data:`ACCELERATOR_TABLE`'s spelling are untouched.

A real ``wx.Dialog`` needs a desktop, so these tests drive a
recording window double and call the steps directly -- the same
``object.__new__`` stand-in ``test_team_editor_dialog_size.py``
uses for its own sizing step. The model-level tests build the real
``ShortcutsListModel``, which needs no ``wx.App``, and patch only
``wx.Platform`` to select the platform arm.
"""

from __future__ import annotations

import pytest
import wx

from rivercrossing.ui.accelerators import Accelerator
from rivercrossing.ui.views import shortcuts
from rivercrossing.ui.views.shortcuts import (
    COL_ACTION,
    COL_KEY,
    SHORTCUT_COLUMN_LABELS,
    SHORTCUT_COLUMN_WIDTHS,
    SHORTCUTS_MIN_SIZE,
    ShortcutsDialog,
    ShortcutsListModel,
    _display_key,
)

# logic-coverage-exempt: T-7 -- the accelerator table's rows and this
# key-spelling transform are pure, but AGENTS.md permits unit tests
# only: no property-based suite may be added without express
# permission, so the platform arms are pinned by parametrized rows.

# The three Ctrl-combo rows of ACCELERATOR_TABLE, and their macOS
# spelling. The five code-side/XRC rows below carry no "Ctrl".
CTRL_COMBO_ROWS: tuple[tuple[str, str], ...] = (
    ("Ctrl+D", "Cmd+D"),
    ("Ctrl+E", "Cmd+E"),
    ("Ctrl+Z", "Cmd+Z"),
)

CTRL_COMBO_IDS: tuple[str, ...] = ("ctrl_d", "ctrl_e", "ctrl_z")

# Every key ACCELERATOR_TABLE spells without "Ctrl", plus the
# empty-string boundary.
PLAIN_KEYS: tuple[str, ...] = ("", "Enter", "F5", "F1", "F2", "Delete")


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


# --------------------------------------------------- the key spelling


@pytest.mark.parametrize(("key", "expected"), CTRL_COMBO_ROWS, ids=CTRL_COMBO_IDS)
def test_shortcuts_display_key_given_mac_and_a_ctrl_combo_spells_cmd(
    key: str, expected: str
) -> None:
    """On macOS the three combos read Cmd -- there is no Ctrl key."""
    assert _display_key(key, is_mac=True) == expected


@pytest.mark.parametrize(
    ("key", "expected"),
    [("Ctrl+D", "Ctrl+D"), ("Ctrl+E", "Ctrl+E"), ("Ctrl+Z", "Ctrl+Z")],
    ids=CTRL_COMBO_IDS,
)
def test_shortcuts_display_key_given_windows_and_a_ctrl_combo_keeps_ctrl(
    key: str, expected: str
) -> None:
    """Off macOS the row reads as ACCELERATOR_TABLE spells it."""
    assert _display_key(key, is_mac=False) == expected


@pytest.mark.parametrize("key", PLAIN_KEYS)
def test_shortcuts_display_key_given_mac_and_a_plain_key_keeps_it(key: str) -> None:
    """Only Ctrl is respelled -- Enter, F5 and Delete are not."""
    assert _display_key(key, is_mac=True) == key


@pytest.mark.parametrize("key", PLAIN_KEYS)
def test_shortcuts_display_key_given_windows_and_a_plain_key_keeps_it(key: str) -> None:
    """The pass-through arm leaves every non-Ctrl key alone."""
    assert _display_key(key, is_mac=False) == key


# --------------------------------------------- the model's Key column


def test_shortcuts_model_given_a_mac_platform_shows_cmd_in_the_key_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Mac-like platform respells the rendered Key cell."""
    monkeypatch.setattr(wx, "Platform", "__WXMAC__")
    row = Accelerator(key="Ctrl+D", action="Delete selected crossing", menu_item_id=None)

    model = ShortcutsListModel((row,))

    assert model.GetValueByRow(0, COL_KEY) == "Cmd+D"


def test_shortcuts_model_given_a_mac_platform_leaves_the_action_column_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Action column renders verbatim -- only Key is transformed.

    The injected action text spells "Ctrl" on purpose: if the
    transform leaked past the Key column, this cell would read
    "Cmd" and the test fails.
    """
    monkeypatch.setattr(wx, "Platform", "__WXMAC__")
    row = Accelerator(key="Ctrl+D", action="Ctrl+D deletes the row", menu_item_id=None)

    model = ShortcutsListModel((row,))

    assert model.GetValueByRow(0, COL_ACTION) == "Ctrl+D deletes the row"


def test_shortcuts_model_given_a_non_mac_platform_shows_ctrl_in_the_key_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Off macOS the Key cell keeps the table's own spelling."""
    monkeypatch.setattr(wx, "Platform", "__WXMSW__")
    row = Accelerator(key="Ctrl+D", action="Delete selected crossing", menu_item_id=None)

    model = ShortcutsListModel((row,))

    assert model.GetValueByRow(0, COL_KEY) == "Ctrl+D"
