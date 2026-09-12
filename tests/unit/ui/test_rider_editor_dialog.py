# SPDX-License-Identifier: GPL-3.0-only
"""Headless 1.0.12 pins for rider_editor_dlg / add_rider_dlg (B1/B2).

The rider editor's form is display-only now: an edit goes through
add_rider_dlg in its "Edit Rider…" mode, so the three text fields are
``wxTE_READONLY`` and the in-form Save button is gone. Everything the
XRC declares is checked here as pure XML -- no ``wx`` import, no
display -- alongside the code-side numbers the XRC cannot express (a
window has no minsize property): the Add/Edit dialog's own width floor
and its primary button's width floor. Real-window geometry and
click-through behaviour stay in the functional suite.
"""

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from defusedxml.ElementTree import parse
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui.views import rider_editor

if TYPE_CHECKING:
    # Type-only: defusedxml does not re-export the Element class.
    # Every parse goes through the defused facade above.
    from xml.etree.ElementTree import Element

XRC_DIR = Path(__file__).resolve().parents[3] / "src" / "rivercrossing" / "ui" / "xrc"

RIDER_EDITOR_DLG = "rider_editor_dlg"
ADD_RIDER_DLG = "add_rider_dlg"


def _dialog(name: str) -> Element:
    """Return the top-level dialog named *name* in riders.xrc."""
    root = parse(XRC_DIR / "riders.xrc").getroot()
    return next(
        obj
        for obj in root.findall("object")
        if obj.get("class") == "wxDialog" and obj.get("name") == name
    )


def _controls(dialog: Element, name: str) -> list[Element]:
    """Return every control object *dialog* declares under *name*."""
    return [obj for obj in dialog.iter("object") if obj.get("name") == name]


def _style(dialog: Element, name: str) -> str:
    """Return the ``<style>`` text of the control named *name*."""
    control = _controls(dialog, name)[0]
    style = control.find("style")
    return style.text if style is not None and style.text is not None else ""


def _button(dialog: Element, name: str) -> Element:
    """Return the ``wxButton`` named *name* inside *dialog*."""
    button = _controls(dialog, name)[0]
    assert button.get("class") == "wxButton"
    return button


def _flex_grid_child_names(dialog: Element) -> list[str]:
    """Return the named controls of the dialog's flex grid, in order."""
    grid = _flex_grid(dialog)
    return [
        child.get("name")
        for item in grid.findall("object")
        for child in item.findall("object")
        if child.get("name") is not None
    ]


def _flex_grid(dialog: Element) -> Element:
    """Return *dialog*'s own ``wxFlexGridSizer``."""
    return next(obj for obj in dialog.iter("object") if obj.get("class") == "wxFlexGridSizer")


def _sizeritems_without_one_object() -> list[str]:
    """Return every sizeritem whose child object count is not one."""
    root = parse(XRC_DIR / "riders.xrc").getroot()
    return [
        item.get("class")
        for item in root.iter("object")
        if item.get("class") == "sizeritem" and len(item.findall("object")) != 1
    ]


# ---------------------------------------------- the read-only form


@pytest.mark.parametrize(
    "control_name",
    ["plate_input", "first_name_input", "last_name_input"],
)
def test_rider_editor_text_field_is_read_only(control_name: str) -> None:
    """Every editor text field is display-only (B1)."""
    assert "wxTE_READONLY" in _style(_dialog(RIDER_EDITOR_DLG), control_name)


def test_rider_editor_team_field_is_a_read_only_text_ctrl() -> None:
    """team_choice is display-only: a read-only ``wxTextCtrl``.

    It replaced the pre-Phase-3 ``wxChoice``: the editor shows which
    team the selected record belongs to and offers no way to change
    it -- team assignment is the Add/Edit dialog's job.
    """
    dialog = _dialog(RIDER_EDITOR_DLG)
    control = _controls(dialog, "team_choice")[0]

    assert control.get("class") == "wxTextCtrl"
    assert "wxTE_READONLY" in _style(dialog, "team_choice")


def test_rider_editor_team_field_is_not_a_combo_box() -> None:
    """A ``wxComboBox`` accepts free-typed names; none exists here."""
    control = _controls(_dialog(RIDER_EDITOR_DLG), "team_choice")[0]

    assert control.get("class") != "wxComboBox"


def test_rider_editor_dialog_declares_no_save_button() -> None:
    """The in-form save path is retired with its button (B1)."""
    dialog = _dialog(RIDER_EDITOR_DLG)

    assert _controls(dialog, "save_btn") == []


def test_rider_editor_dialog_declares_the_edit_button() -> None:
    """edit_btn opens the Edit Rider… dialog (B1)."""
    button = _button(_dialog(RIDER_EDITOR_DLG), "edit_btn")

    assert button.findtext("label") == "Edit Rider…"


def test_rider_editor_dialog_keeps_the_add_button_label() -> None:
    """add_btn survives the rework with its own label (B1)."""
    button = _button(_dialog(RIDER_EDITOR_DLG), "add_btn")

    assert button.findtext("label") == "Add rider…"


# ------------------------------------ the Add/Edit dialog's primary


def test_every_sizeritem_carries_exactly_one_object() -> None:
    """An XRC sizeritem holds exactly one window or sizer.

    The trap this pins (hit while moving the buttons out of the Rider
    box): an element left behind is XML-valid but becomes a *second*
    child object of the enclosing sizeritem, which XRC then ignores --
    so the control silently vanishes from the layout.
    """
    assert _sizeritems_without_one_object() == []


def test_add_rider_dialog_primary_button_reads_save() -> None:
    """One dialog serves both modes, so its primary reads "Save"."""
    button = _button(_dialog(ADD_RIDER_DLG), "wxID_OK")

    assert button.findtext("label") == "Save"


def test_add_rider_dialog_keeps_the_shared_field_names() -> None:
    """Edit mode reuses the window: its field names are unchanged."""
    dialog = _dialog(ADD_RIDER_DLG)

    assert [
        obj.get("name") for obj in dialog.iter("object") if obj.get("class", "").startswith("wxT")
    ] == ["plate_input", "first_name_input", "last_name_input"]


# ------------------------------------ the Add/Edit dialog's Sex row


def test_add_rider_dialog_team_choice_stays_a_choice() -> None:
    """Names are per top-level window: only the editor's changed."""
    control = _controls(_dialog(ADD_RIDER_DLG), "team_choice")[0]

    assert control.get("class") == "wxChoice"


def test_add_rider_dialog_declares_a_sex_choice() -> None:
    """Phase 3: the dialog offers a Sex dropdown (M/F, blank first)."""
    control = _controls(_dialog(ADD_RIDER_DLG), "sex_choice")[0]

    assert control.get("class") == "wxChoice"


def test_add_rider_dialog_sex_choice_follows_the_team_row() -> None:
    """The canvas order is Plate, First name, Last name, Team, Sex."""
    names = _flex_grid_child_names(_dialog(ADD_RIDER_DLG))

    assert names.index("sex_choice") == names.index("team_choice") + 1


def test_add_rider_dialog_sex_row_carries_a_label() -> None:
    """WCAG/UX: every input has a real, persistent label control.

    The label has no frozen name of its own (only the choice does),
    so it is located structurally: the flex grid's item immediately
    before the choice.
    """
    dialog = _dialog(ADD_RIDER_DLG)
    items = _flex_grid(dialog).findall("object")
    index = next(
        i
        for i, item in enumerate(items)
        if any(child.get("name") == "sex_choice" for child in item.findall("object"))
    )
    label = items[index - 1].find("object")
    text = label.findtext("label") if label is not None else None

    assert text == "Sex"


def test_add_rider_dialog_sex_row_keeps_two_columns() -> None:
    """The new row is a label/control pair, not a stray third column."""
    grid = _flex_grid(_dialog(ADD_RIDER_DLG))

    assert (len(grid.findall("object")), grid.findtext("cols")) == (10, "2")


def test_ok_button_min_size_given_a_best_size_triples_only_the_width() -> None:
    """The primary button's floor is 3x its best width (B2)."""
    assert rider_editor.ok_button_min_size((60, 24)) == (180, 24)


@given(
    width=st.integers(min_value=1, max_value=10_000),
    height=st.integers(min_value=1, max_value=400),
)
def test_ok_button_min_size_scales_width_and_preserves_height(width: int, height: int) -> None:
    """The floor never changes the height it is given (T-7)."""
    scaled = rider_editor.ok_button_min_size((width, height))

    assert scaled == (width * rider_editor.OK_BUTTON_SCALE, height)


# -------------------------------- the Add/Edit dialog's width floor


def test_add_rider_width_floor_given_a_narrow_fitted_width_opens_at_the_floor() -> None:
    """A cramped fitted width is floored at ADD_RIDER_MIN_WIDTH."""
    assert rider_editor.add_rider_width_floor((300, 180)) == (700, 180)


@pytest.mark.parametrize(
    ("fitted", "expected"),
    [
        ((0, 0), (700, 0)),  # T-4 boundary: below any real fitted width
        ((1, 1), (700, 1)),  # T-4 boundary: min
        ((699, 5), (700, 5)),  # T-4 boundary: min - 1
        ((700, 7), (700, 7)),  # T-4 boundary: min
        ((701, 9), (701, 9)),  # T-4 boundary: min + 1
        ((1024, 480), (1024, 480)),  # a realistic fitted dialog, already wider
    ],
)
def test_add_rider_width_floor_given_boundary_widths_keeps_the_wider_of_floor_and_fitted(
    fitted: tuple[int, int],
    expected: tuple[int, int],
) -> None:
    """Every width is ``max(ADD_RIDER_MIN_WIDTH, fitted)`` (T-4)."""
    assert rider_editor.add_rider_width_floor(fitted) == expected


@given(
    width=st.integers(min_value=0, max_value=10_000),
    height=st.integers(min_value=0, max_value=400),
)
def test_add_rider_width_floor_given_any_size_preserves_the_height(
    width: int, height: int
) -> None:
    """Property: the floor never changes the dialog's height (T-7)."""
    floored = rider_editor.add_rider_width_floor((width, height))

    assert floored == (max(rider_editor.ADD_RIDER_MIN_WIDTH, width), height)
