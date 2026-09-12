# SPDX-License-Identifier: GPL-3.0-only
"""Phase-5 pins for the blocked-start issues dialog (start_blocked_dlg).

``ConsolePresenter.on_start`` routes a ``StartBlockedError`` to a
custom XRC dialog instead of the W5 native warning: one line per
blocking issue, an OK button, and the ride left in DRAFT. Two halves
are checked here, both cheap and headless:

- the XRC itself, read as pure XML from ``riders.xrc`` (name, title,
  the list control, the single stock OK button);
- the code-side ``StartBlockedListModel``, built and queried directly
  -- a ``DataViewIndexListModel`` needs no ``wx.App``, so the one
  column and its per-row value are unit-testable without a window.

The presenter half (``on_start`` opening that dialog) lives in
``tests/unit/presenters/test_console.py``. Real window geometry and
the modal show stay with the functional suite.

A third, pure half is pinned at the end: the size rule the view
applies after loading the dialog
(:func:`main_frame._start_blocked_size`), so the one "Issue" column
gets room instead of opening at the sizer's narrow best size.
"""

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from defusedxml.ElementTree import parse

from rivercrossing.ui.views import main_frame

if TYPE_CHECKING:
    # Type-only: defusedxml does not re-export the Element class.
    # Every parse goes through the defused facade above.
    from xml.etree.ElementTree import Element

XRC_DIR = Path(__file__).resolve().parents[3] / "src" / "rivercrossing" / "ui" / "xrc"

START_BLOCKED_DLG = "start_blocked_dlg"
START_BLOCKED_LIST = "start_blocked_list"
WX_ID_OK = "wxID_OK"


def _dialog() -> Element:
    """Return riders.xrc's top-level ``start_blocked_dlg`` object."""
    root = parse(XRC_DIR / "riders.xrc").getroot()
    return next(
        obj
        for obj in root.findall("object")
        if obj.get("class") == "wxDialog" and obj.get("name") == START_BLOCKED_DLG
    )


def _controls(name: str) -> list[Element]:
    """Return every object named *name* inside ``start_blocked_dlg``."""
    return [obj for obj in _dialog().iter("object") if obj.get("name") == name]


def _text(element: Element, tag: str) -> str:
    """Return *element*'s ``<tag>`` text, or "" when absent/empty."""
    child = element.find(tag)
    return child.text if child is not None and child.text is not None else ""


def _sizeritem_for(name: str) -> Element:
    """Return the ``sizeritem`` object holding the control named *name*.

    Sizer items are ``<object class="sizeritem">`` in XRC, so the
    search is over every descendant object, not a ``sizeritem`` tag.
    """
    return next(
        item
        for item in _dialog().iter("object")
        if item.get("class") == "sizeritem"
        and any(child.get("name") == name for child in item.iter("object"))
    )


# ------------------------------------------------------------- the XRC


def test_start_blocked_dlg_is_a_resizable_cannot_start_ride_dialog() -> None:
    """The dialog carries the frozen title and resize style."""
    dialog = _dialog()

    assert _text(dialog, "title") == "Cannot Start Ride"
    assert _text(dialog, "style") == "wxDEFAULT_DIALOG_STYLE|wxRESIZE_BORDER"


def test_start_blocked_dlg_declares_exactly_one_issue_list() -> None:
    """One ``wxDataViewCtrl`` named ``start_blocked_list``."""
    lists = _controls(START_BLOCKED_LIST)

    assert [control.get("class") for control in lists] == ["wxDataViewCtrl"]


def test_start_blocked_dlg_expands_the_issue_list_inside_a_vertical_box() -> None:
    """The list takes option 1, bordered, in a vertical box sizer."""
    item = _sizeritem_for(START_BLOCKED_LIST)
    dialog = _dialog()
    boxes = [obj for obj in dialog.iter("object") if obj.get("class") == "wxBoxSizer"]

    assert _text(item, "option") == "1"
    assert _text(item, "flag") == "wxEXPAND|wxALL"
    assert _text(item, "border") == "10"
    assert _text(boxes[0], "orient") == "wxVERTICAL"


def test_start_blocked_dlg_has_a_single_ok_button_in_the_stock_sizer() -> None:
    """The one button is stock ``wxID_OK``, labelled OK."""
    dialog = _dialog()
    buttons = [obj for obj in dialog.iter("object") if obj.get("class") == "wxButton"]

    assert [button.get("name") for button in buttons] == [WX_ID_OK]
    assert _text(buttons[0], "label") == "OK"
    assert _sizeritem_for(WX_ID_OK).find(".//object[@class='wxStdDialogButtonSizer']") is not None


# ------------------------------------------------------- the list model


def test_start_blocked_list_model_given_no_reasons_has_one_empty_column() -> None:
    """The column exists even with no rows to draw."""
    model = main_frame.StartBlockedListModel([])

    assert model.GetColumnCount() == 1
    assert model.GetCount() == 0


def test_start_blocked_list_model_given_one_reason_renders_it_in_the_issue_column() -> None:
    """Row 0's single cell is that reason string."""
    model = main_frame.StartBlockedListModel(["roster has no riders"])

    assert model.GetValueByRow(0, 0) == "roster has no riders"
    assert model.GetColumnType(0) == "string"


def test_start_blocked_list_model_given_many_reasons_keeps_their_order() -> None:
    """One row per reason, in the order the engine reported them."""
    model = main_frame.StartBlockedListModel(
        ["venue is required", "scorer is required", "7: team size must be at least 2, got 1"]
    )

    assert model.GetCount() == 3
    assert model.GetValueByRow(0, 0) == "venue is required"
    assert model.GetValueByRow(1, 0) == "scorer is required"
    assert model.GetValueByRow(2, 0) == "7: team size must be at least 2, got 1"


# ------------------------------------------------------- the size rule


@pytest.mark.parametrize(
    ("fitted", "expected"),
    [
        ((0, 0), (0, 0)),
        ((1, 1), (2, 2)),
        ((400, 300), (800, 600)),
        ((1920, 1080), (3840, 2160)),
    ],
    ids=["empty", "single_dip", "nominal", "large"],
)
def test_start_blocked_size_given_a_fitted_size_returns_double_both_dimensions(
    fitted: tuple[int, int], expected: tuple[int, int]
) -> None:
    """The dialog opens at twice the fitted width and height."""
    result = main_frame._start_blocked_size(fitted)

    assert result == expected
