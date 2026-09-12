# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the Add Team dialog's own default size.

``add_team_dlg`` opened at whatever a plain ``Fit()`` measured --
~228x314 on the operator's macOS display -- which reads cramped for
the record form it carries. ``AddTeamDialog._apply_min_size`` now
floors *and* opens the dialog at
:data:`team_editor.ADD_TEAM_MIN_SIZE` (the operator's own 2x-width /
1.5x-height floor over that measurement), bounded by
:func:`~rivercrossing.ui.views._support.clamp_to_display` so the floor
can never exceed the display work area.

A real ``wx.Dialog`` needs a desktop, so these tests drive a recording
window double and call the sizing step directly -- the same shape
``tests/unit/ui/test_dialogs_positioning.py`` uses for the other
dialogs' own ``_apply_min_size``. ``clamp_to_display`` reads
``wx.GetClientDisplayRect()``, so the work area is stubbed here: the
clamp's own maths still runs, against a fixed rect rather than
whatever display happens to run the suite.
"""

from unittest.mock import patch

import pytest
import wx
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui.views.team_editor import ADD_TEAM_MIN_SIZE, AddTeamDialog

# The operator's floor: 228x2 wide, 314x1.5 tall.
_OPERATOR_FLOOR = (456, 471)

# A work area roomy enough for the floor, so these tests observe the
# un-clamped floor itself.
_ROOMY_DISPLAY = (0, 34, 1920, 1080)

# A work area smaller than the floor: the clamp must win.
_CRAMPED_DISPLAY = (0, 34, 300, 320)


def _stub_display(monkeypatch: pytest.MonkeyPatch, rect: tuple[int, int, int, int]) -> None:
    """Point ``wx.GetClientDisplayRect`` at *rect* for one test."""
    monkeypatch.setattr(wx, "GetClientDisplayRect", lambda: rect)


class _SizingDialog:
    """A dialog double recording every sizing call the SUT makes."""

    def __init__(self, fitted: tuple[int, int] = (228, 314)) -> None:
        """Report *fitted* from GetSize, like a just-Fit() dialog."""
        self._fitted = wx.Size(*fitted)
        self.calls: list[str] = []
        self.min_size: wx.Size | None = None
        self.size: wx.Size | None = None

    def Fit(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the fitting call."""
        self.calls.append("Fit")

    def GetSize(self) -> wx.Size:  # noqa: N802 -- wx API name the SUT calls
        """Record the read and report the fitted size."""
        self.calls.append("GetSize")
        return wx.Size(self._fitted)

    def SetMinSize(self, size: wx.Size) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the floor the SUT applied."""
        self.calls.append("SetMinSize")
        self.min_size = size

    def SetSize(self, size: wx.Size) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the size the SUT opened the dialog at."""
        self.calls.append("SetSize")
        self.size = size


def _dialog_over(dialog: _SizingDialog) -> AddTeamDialog:
    """Return an ``AddTeamDialog`` over *dialog*, no .xrc window loaded.

    ``__init__`` binds every control the .xrc window carries, which
    needs a desktop; the sizing step beside it reads only
    ``self.dialog``, so the instance is made without it -- the same
    kind of stand-in ``test_dialogs_positioning.py`` uses.
    """
    view = object.__new__(AddTeamDialog)
    view.dialog = dialog
    return view


def test_add_team_dialog_min_size_is_the_operators_two_by_one_and_a_half_floor() -> None:
    """228x2 wide, 314x1.5 tall -- the concrete floor this task set."""
    assert ADD_TEAM_MIN_SIZE == _OPERATOR_FLOOR


# T-4 boundaries: below the floor, one pixel under, exactly at it,
# one pixel over, and the measured dialog this task grew.
_FITTED_CASES: list[tuple[tuple[int, int], tuple[int, int]]] = [
    ((0, 0), _OPERATOR_FLOOR),
    ((455, 470), _OPERATOR_FLOOR),
    ((456, 471), _OPERATOR_FLOOR),
    ((457, 472), (457, 472)),
    ((228, 314), _OPERATOR_FLOOR),
    ((640, 480), (640, 480)),
]


@pytest.mark.parametrize(("fitted", "expected"), _FITTED_CASES)
def test_add_team_dialog_apply_min_size_given_any_fitted_size_floors_it_at_the_minimum(
    monkeypatch: pytest.MonkeyPatch,
    fitted: tuple[int, int],
    expected: tuple[int, int],
) -> None:
    """The dialog's floor is never below its own minimum."""
    _stub_display(monkeypatch, _ROOMY_DISPLAY)
    dialog = _SizingDialog(fitted=fitted)

    _dialog_over(dialog)._apply_min_size()

    assert (dialog.min_size.width, dialog.min_size.height) == expected


@pytest.mark.parametrize(("fitted", "expected"), _FITTED_CASES)
def test_add_team_dialog_apply_min_size_given_any_fitted_size_opens_it_at_the_minimum(
    monkeypatch: pytest.MonkeyPatch,
    fitted: tuple[int, int],
    expected: tuple[int, int],
) -> None:
    """The dialog opens at its floor, not merely bounded by it."""
    _stub_display(monkeypatch, _ROOMY_DISPLAY)
    dialog = _SizingDialog(fitted=fitted)

    _dialog_over(dialog)._apply_min_size()

    assert (dialog.size.width, dialog.size.height) == expected


def test_add_team_dialog_apply_min_size_measures_the_fitted_size_before_flooring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scale what Fit() just measured, never a stale size."""
    _stub_display(monkeypatch, _ROOMY_DISPLAY)
    dialog = _SizingDialog(fitted=(228, 314))

    _dialog_over(dialog)._apply_min_size()

    assert dialog.calls == ["Fit", "GetSize", "SetMinSize", "SetSize"]


def test_add_team_dialog_apply_min_size_given_a_cramped_display_clamps_to_the_work_area(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A display smaller than the floor still wins the clamp."""
    _stub_display(monkeypatch, _CRAMPED_DISPLAY)
    dialog = _SizingDialog(fitted=(228, 314))

    _dialog_over(dialog)._apply_min_size()

    assert (dialog.min_size.width, dialog.min_size.height) == (300, 320)
    assert (dialog.size.width, dialog.size.height) == (300, 320)


@given(
    width=st.integers(min_value=0, max_value=1_000),
    height=st.integers(min_value=0, max_value=1_000),
)
def test_add_team_dialog_apply_min_size_given_any_fitted_size_keeps_the_larger_of_the_two(
    width: int,
    height: int,
) -> None:
    """T-7 property: the roomy-display floor is the component-wise max.

    Both dimensions stay under ``_ROOMY_DISPLAY``, so the clamp is
    never the thing being measured here (its own test covers that).
    """
    # A context manager, not the monkeypatch fixture: Hypothesis does
    # not reset a function-scoped fixture between generated inputs.
    with patch.object(wx, "GetClientDisplayRect", lambda: _ROOMY_DISPLAY):
        dialog = _SizingDialog(fitted=(width, height))

        _dialog_over(dialog)._apply_min_size()

    assert (dialog.min_size.width, dialog.min_size.height) == (
        max(ADD_TEAM_MIN_SIZE[0], width),
        max(ADD_TEAM_MIN_SIZE[1], height),
    )
