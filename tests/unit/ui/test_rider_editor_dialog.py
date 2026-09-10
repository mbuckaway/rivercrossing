# SPDX-License-Identifier: GPL-3.0-only
"""Headless 1.0.12 pins for rider_editor_dlg / add_rider_dlg (B1/B2).

The rider editor's form is display-only now: an edit goes through
add_rider_dlg in its "Edit Rider…" mode, so the three text fields are
``wxTE_READONLY`` and the in-form Save button is gone. Everything the
XRC declares is checked here as pure XML -- no ``wx`` import, no
display -- alongside the one code-side number the XRC cannot express
(a window has no minsize property), the Add/Edit primary button's
width floor. Real-window geometry and click-through behaviour stay in
the functional suite.
"""

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui.views import rider_editor

XRC_DIR = Path(__file__).resolve().parents[3] / "src" / "rivercrossing" / "ui" / "xrc"

RIDER_EDITOR_DLG = "rider_editor_dlg"
ADD_RIDER_DLG = "add_rider_dlg"


def _dialog(name: str) -> ET.Element:
    """Return the top-level dialog named *name* in riders.xrc."""
    # S314: the project's own XRC, not untrusted input.
    root = ET.parse(XRC_DIR / "riders.xrc").getroot()  # noqa: S314
    return next(
        obj
        for obj in root.findall("object")
        if obj.get("class") == "wxDialog" and obj.get("name") == name
    )


def _controls(dialog: ET.Element, name: str) -> list[ET.Element]:
    """Return every control object *dialog* declares under *name*."""
    return [obj for obj in dialog.iter("object") if obj.get("name") == name]


def _style(dialog: ET.Element, name: str) -> str:
    """Return the ``<style>`` text of the control named *name*."""
    control = _controls(dialog, name)[0]
    style = control.find("style")
    return style.text if style is not None and style.text is not None else ""


def _button(dialog: ET.Element, name: str) -> ET.Element:
    """Return the ``wxButton`` named *name* inside *dialog*."""
    button = _controls(dialog, name)[0]
    assert button.get("class") == "wxButton"
    return button


def _sizeritems_without_one_object() -> list[str]:
    """Return every sizeritem whose child object count is not one."""
    # S314: the project's own XRC, not untrusted input.
    root = ET.parse(XRC_DIR / "riders.xrc").getroot()  # noqa: S314
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


def test_rider_editor_team_field_is_a_non_editable_choice() -> None:
    """team_choice is a wxChoice: selectable, never free-typed (B1).

    XRC's wxChoice is the read-only Team field -- wxComboBox is the
    type that would accept free text, and it appears nowhere here.
    """
    control = _controls(_dialog(RIDER_EDITOR_DLG), "team_choice")[0]

    assert control.get("class") == "wxChoice"


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
