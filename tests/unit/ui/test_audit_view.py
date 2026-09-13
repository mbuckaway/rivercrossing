# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for ``audit_dlg``'s columns and size (Phase 6).

``audit.xrc`` declares no ``<size>`` and XRC has no window-level
minsize, so the audit dialog used to open at whatever a plain ``Fit()``
measured and its five columns at the platform's 80 DIP default -- the
Reason cell clipped at every window size. ``AuditDialog`` now appends
each column at its own pinned width (When 90 | Who 120 | Action 180 |
Entry 120 | Reason 330) and ``_apply_min_size`` floors *and* opens the
dialog at :data:`audit.MIN_SIZE` (1000x600, ~2x the measured content),
bounded by :func:`~rivercrossing.ui.views._support.clamp_to_display`
so a small screen still shows the whole dialog.

A real ``wx.Dialog`` needs a desktop, so these tests drive recording
doubles and call the two steps directly -- the same shape
``test_dialogs_positioning.py`` and ``test_team_editor_dialog_size.py``
use for the other dialogs. ``clamp_to_display`` reads the live display
through ``wx.GetClientDisplayRect``, which raises without a
``wx.App``, so the work area is stubbed: that read is the sizing
step's GUI I/O boundary (T-10), not its logic.
"""

from unittest.mock import patch

import pytest
import wx
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui.views.audit import (
    AUDIT_COLUMN_LABELS,
    AUDIT_COLUMN_WIDTHS,
    MIN_SIZE,
    AuditDialog,
)

# A work area roomy enough for the dialog's own 1000x600 floor.
_ROOMY_DISPLAY = (0, 34, 1920, 1080)

# A work area smaller than the floor: the clamp must win.
_CRAMPED_DISPLAY = (0, 34, 800, 480)


def _stub_display(monkeypatch: pytest.MonkeyPatch, rect: tuple[int, int, int, int]) -> None:
    """Point ``wx.GetClientDisplayRect`` at *rect* for one test."""
    monkeypatch.setattr(wx, "GetClientDisplayRect", lambda: rect)


class _RecordingList:
    """An ``audit_list`` double recording each appended column."""

    def __init__(self) -> None:
        """Start with no columns."""
        self.columns: list[tuple[str, int, int]] = []

    def AppendTextColumn(  # noqa: N802 -- wx API name the SUT calls
        self, label: str, col: int, *, width: int
    ) -> None:
        """Record one appended text column and its pinned width."""
        self.columns.append((label, col, width))


class _SizingDialog:
    """A dialog double recording every sizing call the SUT makes."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[str] = []
        self.min_size: wx.Size | None = None

    def Fit(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the fitting call."""
        self.calls.append("Fit")

    def SetMinSize(self, size: wx.Size) -> None:  # noqa: N802 -- wx API name
        """Record the floor the SUT applied."""
        self.calls.append("SetMinSize")
        self.min_size = size


def _dialog_over(dialog: _SizingDialog) -> AuditDialog:
    """Return an ``AuditDialog`` over *dialog*, no window loaded."""
    view = object.__new__(AuditDialog)
    view.dialog = dialog
    return view


# ------------------------------------------------------------- columns


def test_audit_column_widths_given_the_canvas_order_are_the_pinned_pixels() -> None:
    """When 90 | Who 120 | Action 180 | Entry 120 | Reason 330."""
    assert AUDIT_COLUMN_WIDTHS == (90, 120, 180, 120, 330)


def test_audit_column_widths_given_the_five_labels_carry_one_width_each() -> None:
    """One width per label: the column index never misaligns."""
    assert len(AUDIT_COLUMN_WIDTHS) == len(AUDIT_COLUMN_LABELS)


def test_audit_dialog_build_columns_given_the_labels_appends_each_width() -> None:
    """Every column is appended with its label, index and width."""
    listing = _RecordingList()
    view = object.__new__(AuditDialog)
    view.audit_list = listing

    view._build_columns()

    assert listing.columns == [
        (label, col, AUDIT_COLUMN_WIDTHS[col]) for col, label in enumerate(AUDIT_COLUMN_LABELS)
    ]


def test_audit_column_widths_given_the_reason_column_leave_it_the_widest() -> None:
    """The Reason cell carries arbitrary text, so it takes the slack."""
    assert AUDIT_COLUMN_WIDTHS[-1] == max(AUDIT_COLUMN_WIDTHS)


# ------------------------------------------------------------- min size


def test_audit_dialog_min_size_given_the_fitted_content_is_1000_by_600() -> None:
    """Part 5's own numbers: 2x the dialog's measured content."""
    assert MIN_SIZE == (1000, 600)


def test_audit_dialog_apply_min_size_given_a_roomy_display_floors_and_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SetMinSize is what floors it; Fit() is what grows it now."""
    _stub_display(monkeypatch, _ROOMY_DISPLAY)
    dialog = _SizingDialog()

    _dialog_over(dialog)._apply_min_size()

    assert (dialog.min_size.width, dialog.min_size.height, dialog.calls) == (
        1000,
        600,
        ["SetMinSize", "Fit"],
    )


def test_audit_dialog_apply_min_size_given_a_cramped_display_clamps_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A display smaller than the floor still gets a whole dialog."""
    _stub_display(monkeypatch, _CRAMPED_DISPLAY)
    dialog = _SizingDialog()

    _dialog_over(dialog)._apply_min_size()

    assert (dialog.min_size.width, dialog.min_size.height) == (800, 480)


@pytest.mark.parametrize(
    ("rect", "expected"),
    [
        ((0, 34, 999, 599), (999, 599)),  # T-4: min - 1 on both axes
        ((0, 34, 1000, 600), (1000, 600)),  # T-4: exactly the floor
        ((0, 34, 1001, 601), (1000, 600)),  # T-4: min + 1, the floor holds
        ((0, 34, 3840, 2160), (1000, 600)),  # a 4K display
    ],
    ids=["min_minus_one", "min", "min_plus_one", "roomy"],
)
def test_audit_dialog_apply_min_size_given_any_display_fits_the_area(
    monkeypatch: pytest.MonkeyPatch,
    rect: tuple[int, int, int, int],
    expected: tuple[int, int],
) -> None:
    """T-4 boundaries: the floor is the smaller of it and the area."""
    _stub_display(monkeypatch, rect)
    dialog = _SizingDialog()

    _dialog_over(dialog)._apply_min_size()

    assert (dialog.min_size.width, dialog.min_size.height) == expected


@given(size=st.tuples(st.integers(0, 4000), st.integers(0, 4000)))
def test_audit_dialog_apply_min_size_given_any_display_keeps_it_inside(
    size: tuple[int, int],
) -> None:
    """T-7 invariant: the applied floor never exceeds the work area."""
    # A context manager, not the monkeypatch fixture: Hypothesis does
    # not reset a function-scoped fixture between generated inputs.
    with patch.object(wx, "GetClientDisplayRect", lambda: (0, 34, *size)):
        dialog = _SizingDialog()

        _dialog_over(dialog)._apply_min_size()

    assert dialog.min_size.width <= size[0]
    assert dialog.min_size.height <= size[1]
