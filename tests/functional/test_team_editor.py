# SPDX-License-Identifier: GPL-3.0-only
"""Functional tests for team_editor_dlg (Phase 4 rework), live roster.

Drives the actual ``team_editor_dlg`` XRC dialog wired to
``rivercrossing.ui.views.team_editor.TeamEditor``, which holds a
real, in-memory :class:`~rivercrossing.roster.Roster` and drives it
through :class:`~rivercrossing.ui.presenters.teams.TeamsPresenter` --
the exemplar shape is ``test_selftest_dialog.py``'s
``_show``/try-finally pattern (and ``test_rider_editor.py``'s own
sibling): never a mock in place of the real Roster or the real
dialog.

The rework asserts: the three ``Team | Riders | Logo`` columns
(built code-side over ``wxDataViewCtrl``) with rider counts and the
logo *kind* text (``Card``/``Image``/blank), the real-bitmap
``logo_bmp`` preview, the multi-line Notes field with a three-line
minimum, the panes' action-button relocation (Add bottom-right,
Save/Remove/Close bottom-left), and Add reading the form directly --
no native name prompt -- with staged-logo picks when nothing is
selected.

``_lists_common.demo_seeded_roster`` seeds its roster with
``team_logo_seed=8843`` (Phase 4), so the seeded Trail Blazers team
carries the deterministic first logo card and its Logo cell reads
``Card``.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

import harness
import pytest
import wx
from _lists_common import demo_seeded_roster
from PIL import Image

from rivercrossing.cards import seeded_card_codes
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.ui import ids
from rivercrossing.ui.views.team_editor import CARD_TEXT, IMAGE_TEXT, TEAMS_INFOBAR, TeamEditor

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.functional

_SEED = 8843
_COL_TEAM = 0
_COL_RIDERS = 1
_COL_LOGO = 2

# The rework's wxStaticBitmap preview, resolved through the generated
# ui/ids.py registry (logo_bmp is XRC-authored in teams.xrc).
LOGO_BMP = ids.LOGO_BMP


def _show(xrc_resource: Any, roster: Roster) -> tuple[Any, TeamEditor]:  # noqa: ANN401
    """Load team_editor_dlg, wire it live over *roster*, show, pump."""
    dialog = harness.load_window_verified(xrc_resource, ids.TEAM_EDITOR_DLG, frame=False)
    try:
        view = TeamEditor(dialog, roster=roster)
        dialog.Show()
        harness.pump()
    except Exception:  # Fault A: any post-load failure must close the dialog
        harness.close_window(dialog)
        raise
    return dialog, view


def _teams_list_rows(dialog: Any) -> tuple[tuple[str, str, str], ...]:  # noqa: ANN401
    """Return every teams_list row as (name, riders, logo) text."""
    model = harness.find_control(dialog, ids.TEAMS_LIST).GetModel()
    return tuple(
        tuple(model.GetValueByRow(row, col) for col in (0, 1, 2))
        for row in range(model.GetCount())
    )


def _member_rows(dialog: Any) -> tuple[str, ...]:  # noqa: ANN401
    """Return every members_list row's text."""
    model = harness.find_control(dialog, ids.MEMBERS_LIST).GetModel()
    return tuple(model.GetValueByRow(row, 0) for row in range(model.GetCount()))


def _name_input_value(dialog: Any) -> str:  # noqa: ANN401 -- wx ships no stubs
    """Return name_input's current text."""
    return harness.find_control(dialog, ids.NAME_INPUT).GetValue()


def _logo_bitmap(dialog: Any) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return logo_bmp's current bitmap."""
    return harness.find_control(dialog, LOGO_BMP).GetBitmap()


def _seeded_rows() -> tuple[tuple[str, str, str], ...]:
    """Return the seeded roster's expected (name, riders, logo) rows."""
    return (("Trail Blazers", "2", CARD_TEXT),)


def _tiny_logo_bytes() -> bytes:
    """Write a deterministic 8x8 solid PNG for a logo-image pick.

    PIL writes a standard RGBA PNG that wx's decoder accepts (the
    console_subprocess_scenarios.py pattern); the repo never commits
    PNGs, so the bytes are generated at runtime.
    """
    buffer = io.BytesIO()
    Image.new("RGBA", (8, 8), (30, 90, 160, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


# ------------------------------------------------------- the infobar


def test_team_editor_dlg_infobar_disables_show_hide_effects(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """teams_infobar disables both slide effects (rider_editor pin)."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        bar = harness.find_control(dialog, TEAMS_INFOBAR)
        effects = (bar.GetShowEffect(), bar.GetHideEffect())
    finally:
        harness.close_window(dialog)

    assert effects == (wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)


# ------------------------------------------------------------- opening


def test_team_editor_dlg_opens_with_team_riders_and_logo_columns(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """The list shows Team | Riders | Logo and the logo kinds."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        control = harness.find_control(dialog, ids.TEAMS_LIST)
        titles = tuple(
            control.GetColumn(col).GetTitle() for col in range(control.GetColumnCount())
        )
        rows = _teams_list_rows(dialog)
        blank = _logo_bitmap(dialog)
    finally:
        harness.close_window(dialog)

    assert titles == ("Team", "Riders", "Logo")
    assert rows == _seeded_rows()
    assert blank.IsOk() is False  # no selection: no logo preview yet


def test_team_editor_dlg_selecting_a_team_fills_the_form_members_and_logo(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Row selection shows the record, members and a card bitmap."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.select_row(dialog, ids.TEAMS_LIST, 0)
        name = _name_input_value(dialog)
        members = _member_rows(dialog)
        bitmap = _logo_bitmap(dialog)
    finally:
        harness.close_window(dialog)

    assert name == "Trail Blazers"
    assert members == ("A. Roy", "K. Singh")
    assert bitmap.IsOk() is True


# ----------------------------------------------------------------- add


def test_team_editor_dlg_add_creates_a_team_typed_into_the_form(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Add team reads the form's name -- no native prompt involved."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.type_text(dialog, ids.NAME_INPUT, "Dirt Dynamos")
        harness.click(dialog, ids.ADD_BTN)
        rows = _teams_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert [row[0] for row in rows] == ["Trail Blazers", "Dirt Dynamos"]
    assert rows[1][1] == "1"
    assert rows[1][2] == CARD_TEXT


def test_team_editor_dlg_add_given_a_blank_name_refuses_via_the_infobar(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Add with an empty form creates nothing and says so."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.click(dialog, ids.ADD_BTN)
        infobar_shown = harness.find_control(dialog, TEAMS_INFOBAR).IsShown()
        rows = _teams_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert infobar_shown is True
    assert rows == _seeded_rows()


def test_team_editor_dlg_add_given_a_duplicate_name_refuses_via_the_infobar(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A name another team already carries is refused, case-folded."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.type_text(dialog, ids.NAME_INPUT, "  trail blazers  ")
        harness.click(dialog, ids.ADD_BTN)
        infobar_shown = harness.find_control(dialog, TEAMS_INFOBAR).IsShown()
        rows = _teams_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert infobar_shown is True
    assert rows == _seeded_rows()


# --------------------------------------------------------------- save


def test_team_editor_dlg_save_renames_the_selected_team(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Select a team, edit its name, Save -- that row updates."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.select_row(dialog, ids.TEAMS_LIST, 0)
        harness.type_text(dialog, ids.NAME_INPUT, "Moss Ridge Riders")
        harness.click(dialog, ids.SAVE_BTN)
        rows = _teams_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert rows[0][0] == "Moss Ridge Riders"


def test_team_editor_dlg_save_refuses_a_duplicate_rename_via_the_infobar(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Renaming a team onto another team's name is refused on Save."""
    roster = demo_seeded_roster()
    roster.create_team_entry_of_one(
        display_name="Dirt Dynamos",
        rider=Rider(first_name="Z.", last_name="Wu", plate="79"),
    )
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.select_row(dialog, ids.TEAMS_LIST, 0)
        harness.type_text(dialog, ids.NAME_INPUT, "dirt dynamos")
        harness.click(dialog, ids.SAVE_BTN)
        infobar_shown = harness.find_control(dialog, TEAMS_INFOBAR).IsShown()
        rows = _teams_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert infobar_shown is True
    assert rows[0][0] == "Trail Blazers"


def test_team_editor_dlg_save_persists_notes_for_the_selected_team(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """The Notes field survives a Save/row refresh."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.select_row(dialog, ids.TEAMS_LIST, 0)
        harness.type_text(dialog, ids.NOTES_INPUT, "second wave")
        harness.click(dialog, ids.SAVE_BTN)
        harness.select_row(dialog, ids.TEAMS_LIST, 0)
        notes = harness.find_control(dialog, ids.NOTES_INPUT).GetValue()
    finally:
        harness.close_window(dialog)

    assert notes == "second wave"


# ------------------------------------------------------------- remove


def test_team_editor_dlg_remove_deletes_the_selected_draft_team(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Remove on a DRAFT team with no data removes its row (R-15)."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.select_row(dialog, ids.TEAMS_LIST, 0)
        harness.click(dialog, ids.REMOVE_BTN)
        rows = _teams_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert rows == ()


# --------------------------------------------------- relay plate row


def _relay_roster() -> Roster:
    """Return a seeded MIXED team_relay DRAFT roster with one team."""
    roster = Roster(
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.TEAM_RELAY,
        team_logo_seed=_SEED,
    )
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_team_entry(
        display_name="Moss Ridge",
        riders=[
            Rider(first_name="R.", last_name="Dubois"),
            Rider(first_name="M.", last_name="Chen"),
        ],
        plate="88",
    )
    return roster


def _solo_only_roster() -> Roster:
    """Return a bare solo-only roster (no teams possible, R-11)."""
    roster = Roster()
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    return roster


@pytest.mark.parametrize(
    ("roster_factory", "expected_visible"),
    [(_solo_only_roster, False), (demo_seeded_roster, False), (_relay_roster, True)],
    ids=["solo_only", "mixed_pooled", "mixed_relay"],
)
def test_team_editor_dlg_relay_plate_row_visibility_matches_plate_model(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    roster_factory: Callable[[], Roster],
    expected_visible: bool,  # noqa: FBT001 -- a parametrize row's value, not a call-site bool
) -> None:
    """Plate (relay) shows on team_relay rides only (S1/R-16)."""
    dialog, _view = _show(xrc_resource, roster_factory())

    try:
        row_shown = harness.find_control(dialog, ids.RELAY_PLATE_INPUT).IsShown()
    finally:
        harness.close_window(dialog)

    assert row_shown is expected_visible


def test_team_editor_dlg_add_on_a_solo_only_ride_refuses_via_the_infobar(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """R-11: a solo-only roster refuses Add with the reason shown."""
    roster = _solo_only_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.type_text(dialog, ids.NAME_INPUT, "Dirt Dynamos")
        harness.click(dialog, ids.ADD_BTN)
        infobar_shown = harness.find_control(dialog, TEAMS_INFOBAR).IsShown()
        rows = _teams_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert infobar_shown is True
    assert rows == ()


# -------------------------------------------------------- the notes box


def test_team_editor_dlg_notes_input_is_multiline_with_a_three_line_minimum(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Notes accepts wrapped multi-line text and shows >= 3 lines."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        notes = harness.find_control(dialog, ids.NOTES_INPUT)
        multiline = bool(notes.GetWindowStyleFlag() & wx.TE_MULTILINE)
        height = notes.GetSize().height
        three_lines = 3 * notes.GetCharHeight()
        notes.SetValue("line one\nline two\nline three")
        line_count = notes.GetNumberOfLines()
    finally:
        harness.close_window(dialog)

    assert multiline is True
    assert height >= three_lines
    assert line_count == 3


# ------------------------------------------------------- logo preview


def test_team_editor_dlg_pick_card_advances_the_selected_teams_logo(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Each Pick card click walks the seeded sequence."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)
    codes = seeded_card_codes(_SEED)

    try:
        harness.select_row(dialog, ids.TEAMS_LIST, 0)
        harness.click(dialog, ids.PICK_CARD_BTN)
        rows = _teams_list_rows(dialog)
        bitmap = _logo_bitmap(dialog)
        team = next(entry for entry in roster.entries if entry.display_name == "Trail Blazers")
    finally:
        harness.close_window(dialog)

    assert rows[0] == ("Trail Blazers", "2", CARD_TEXT)
    assert team.logo_card == codes[1]
    assert bitmap.IsOk() is True


def test_team_editor_dlg_image_btn_loads_the_picked_bytes_and_image_wins(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Image… picks a real PNG; the cell reads Image."""
    logo_bytes = _tiny_logo_bytes()
    logo_file = tmp_path / "logo.png"
    logo_file.write_bytes(logo_bytes)
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)
    monkeypatch.setattr(
        "rivercrossing.ui.views.team_editor.pick_logo_image_path", lambda _parent: logo_file
    )

    try:
        harness.select_row(dialog, ids.TEAMS_LIST, 0)
        harness.click(dialog, ids.IMAGE_BTN)
        rows = _teams_list_rows(dialog)
        bitmap = _logo_bitmap(dialog)
        team = next(entry for entry in roster.entries if entry.display_name == "Trail Blazers")
    finally:
        harness.close_window(dialog)

    assert rows[0] == ("Trail Blazers", "2", IMAGE_TEXT)
    assert team.logo_png == logo_bytes
    assert team.logo_card is None
    assert bitmap.IsOk() is True


def test_team_editor_dlg_pick_card_in_add_mode_stages_the_logo_for_the_add(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Picking a card with no selection previews it; Add applies it."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)
    codes = seeded_card_codes(_SEED)

    try:
        harness.click(dialog, ids.PICK_CARD_BTN)
        staged_bitmap = _logo_bitmap(dialog)
        harness.type_text(dialog, ids.NAME_INPUT, "Dirt Dynamos")
        harness.click(dialog, ids.ADD_BTN)
        rows = _teams_list_rows(dialog)
        team = next(entry for entry in roster.entries if entry.display_name == "Dirt Dynamos")
    finally:
        harness.close_window(dialog)

    assert staged_bitmap.IsOk() is True
    assert rows[1] == ("Dirt Dynamos", "1", CARD_TEXT)
    assert team.logo_card == codes[1]
    assert team.logo_png is None


def test_team_editor_dlg_image_btn_in_add_mode_stages_the_logo_for_the_add(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An image staged with no selection previews; Add applies it."""
    logo_bytes = _tiny_logo_bytes()
    logo_file = tmp_path / "logo.png"
    logo_file.write_bytes(logo_bytes)
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)
    monkeypatch.setattr(
        "rivercrossing.ui.views.team_editor.pick_logo_image_path", lambda _parent: logo_file
    )

    try:
        harness.click(dialog, ids.IMAGE_BTN)
        staged_bitmap = _logo_bitmap(dialog)
        harness.type_text(dialog, ids.NAME_INPUT, "Dirt Dynamos")
        harness.click(dialog, ids.ADD_BTN)
        rows = _teams_list_rows(dialog)
        team = next(entry for entry in roster.entries if entry.display_name == "Dirt Dynamos")
    finally:
        harness.close_window(dialog)

    assert staged_bitmap.IsOk() is True
    assert rows[1] == ("Dirt Dynamos", "1", IMAGE_TEXT)
    assert team.logo_png == logo_bytes
    assert team.logo_card is None


# ------------------------------------------------------ pane layout


def test_team_editor_dlg_action_buttons_sit_below_their_own_panes(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Add sits bottom-right; Save/Remove/Close bottom-left (rework)."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        teams = harness.find_control(dialog, ids.TEAMS_LIST)
        members = harness.find_control(dialog, ids.MEMBERS_LIST)
        add = harness.find_control(dialog, ids.ADD_BTN)
        save = harness.find_control(dialog, ids.SAVE_BTN)
        remove = harness.find_control(dialog, ids.REMOVE_BTN)
        close = harness.find_control(dialog, "wxID_CLOSE")
        # Screen coords: the controls have different parents, so
        # parent-relative positions are not comparable (measured).
        # Every value is captured before close_window(): Get* on a
        # destroyed control segfaults (measured on CI).
        left_x = teams.GetScreenPosition().x
        right_x = members.GetScreenPosition().x
        teams_bottom = teams.GetScreenPosition().y + teams.GetSize().height
        members_bottom = members.GetScreenPosition().y + members.GetSize().height
        save_x, save_y = save.GetScreenPosition().x, save.GetScreenPosition().y
        remove_x, remove_y = remove.GetScreenPosition().x, remove.GetScreenPosition().y
        close_x, close_y = close.GetScreenPosition().x, close.GetScreenPosition().y
        add_x, add_y = add.GetScreenPosition().x, add.GetScreenPosition().y
    finally:
        harness.close_window(dialog)

    # Save and Remove open the left column's bottom row; Close shares
    # that row (its stock sizer may inset it a few px, so the claim is
    # "left pane", not "flush at left_x"). Add team anchors the right
    # pane's bottom row.
    assert save_x < right_x
    assert remove_x < right_x
    assert left_x <= close_x < right_x
    assert add_x > right_x
    assert save_y > teams_bottom
    assert remove_y > teams_bottom
    assert close_y > teams_bottom
    assert add_y > members_bottom


def test_team_editor_dlg_members_list_height_stays_bounded_when_the_dialog_grows(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Vertical growth goes to teams_list; members_list scrolls."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        teams = harness.find_control(dialog, ids.TEAMS_LIST)
        members = harness.find_control(dialog, ids.MEMBERS_LIST)
        members_before = members.GetSize().height
        teams_before = teams.GetSize().height
        size = dialog.GetSize()
        dialog.SetSize(wx.Size(size.width, size.height + 200))
        dialog.Layout()
        harness.pump()
        members_after = members.GetSize().height
        teams_after = teams.GetSize().height
    finally:
        harness.close_window(dialog)

    assert members_after == members_before
    assert teams_after > teams_before
