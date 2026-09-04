# SPDX-License-Identifier: GPL-3.0-only
"""Functional tests for team_editor_dlg live (Phase 4): real Roster.

Drives the actual ``team_editor_dlg`` XRC dialog wired to
``rivercrossing.ui.views.team_editor.TeamEditor``, which holds a
real, in-memory :class:`~rivercrossing.roster.Roster` and drives it
through :class:`~rivercrossing.ui.presenters.teams.TeamsPresenter` --
the exemplar shape is ``test_selftest_dialog.py``'s
``_show``/try-finally pattern (and ``test_rider_editor.py``'s own
sibling): never a mock in place of the real Roster or the real
dialog.

``_lists_common.demo_seeded_roster`` now seeds its roster with
``team_logo_seed=8843`` (Phase 4) -- the same store-backed shape
production loads -- so the seeded Trail Blazers team carries the
deterministic first logo card, and ``teams_list``'s Logo column has
real text to assert.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import harness
import pytest
import wx
from _lists_common import demo_seeded_roster

from rivercrossing.cards import seeded_card_codes
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.ui import ids
from rivercrossing.ui.views.team_editor import TEAMS_INFOBAR, TeamEditor

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.functional

_SEED = 8843
_COL_TEAM = 0
_COL_LOGO = 1


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


def _teams_list_rows(dialog: Any) -> tuple[tuple[str, str], ...]:  # noqa: ANN401
    """Return every teams_list row as (name, logo) text."""
    model = harness.find_control(dialog, ids.TEAMS_LIST).GetModel()
    return tuple(
        tuple(model.GetValueByRow(row, col) for col in (0, 1)) for row in range(model.GetCount())
    )


def _member_rows(dialog: Any) -> tuple[str, ...]:  # noqa: ANN401
    """Return every members_list row's text."""
    model = harness.find_control(dialog, ids.MEMBERS_LIST).GetModel()
    return tuple(model.GetValueByRow(row, 0) for row in range(model.GetCount()))


def _name_input_value(dialog: Any) -> str:  # noqa: ANN401 -- wx ships no stubs
    """Return name_input's current text."""
    return harness.find_control(dialog, ids.NAME_INPUT).GetValue()


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


def test_team_editor_dlg_opens_showing_the_seeded_teams_with_logo_cards(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """The one seeded team shows with its deterministic logo card."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        rows = _teams_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    logo_code = seeded_card_codes(_SEED)[0]
    assert rows == (("Trail Blazers", _logo_text(logo_code)),)


def test_team_editor_dlg_selecting_a_team_fills_the_form_and_members(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Row selection shows the record form and the read-only members."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.select_row(dialog, ids.TEAMS_LIST, 0)
        name = _name_input_value(dialog)
        members = _member_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert name == "Trail Blazers"
    assert members == ("A. Roy", "K. Singh")


# ----------------------------------------------------------------- add


def test_team_editor_dlg_add_creates_a_team_and_lists_it(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Add team prompts for a name, then the new team appears."""
    roster = demo_seeded_roster()
    dialog, view = _show(xrc_resource, roster)
    monkeypatch.setattr(view, "prompt_team_name", lambda: "Dirt Dynamos")

    try:
        harness.click(dialog, ids.ADD_BTN)
        rows = _teams_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert [row[0] for row in rows] == ["Trail Blazers", "Dirt Dynamos"]


def test_team_editor_dlg_add_given_a_cancelled_prompt_is_a_no_op(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling the native prompt (None) creates nothing (R-20)."""
    roster = demo_seeded_roster()
    dialog, view = _show(xrc_resource, roster)
    monkeypatch.setattr(view, "prompt_team_name", lambda: None)

    try:
        harness.click(dialog, ids.ADD_BTN)
        rows = _teams_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert rows == _seeded_rows()


def _seeded_rows() -> tuple[tuple[str, str], ...]:
    """Return the seeded roster's own expected (name, logo) rows."""
    code = seeded_card_codes(_SEED)[0]
    return (("Trail Blazers", _logo_text(code)),)


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-11: a solo-only roster refuses Add with the reason shown."""
    roster = _solo_only_roster()
    dialog, view = _show(xrc_resource, roster)
    monkeypatch.setattr(view, "prompt_team_name", lambda: "Dirt Dynamos")

    try:
        harness.click(dialog, ids.ADD_BTN)
        infobar_shown = harness.find_control(dialog, TEAMS_INFOBAR).IsShown()
        rows = _teams_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert infobar_shown is True
    assert rows == ()


# ---------------------------------------------- logo card / image


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
        preview = harness.find_control(dialog, ids.LOGO_PREVIEW).GetLabel()
    finally:
        harness.close_window(dialog)

    assert rows[0] == ("Trail Blazers", _logo_text(codes[1]))
    assert preview == _logo_text(codes[1])


def test_team_editor_dlg_image_btn_loads_the_picked_bytes_and_image_wins(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Image… picks a file; the preview swaps to Image."""
    logo_file = tmp_path / "logo.png"
    logo_file.write_bytes(b"team-logo-png")
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)
    monkeypatch.setattr(
        "rivercrossing.ui.views.team_editor.pick_logo_image_path", lambda _parent: logo_file
    )

    try:
        harness.select_row(dialog, ids.TEAMS_LIST, 0)
        harness.click(dialog, ids.IMAGE_BTN)
        preview = harness.find_control(dialog, ids.LOGO_PREVIEW).GetLabel()
        team = next(entry for entry in roster.entries if entry.display_name == "Trail Blazers")
    finally:
        harness.close_window(dialog)

    assert preview == "Image"
    assert team.logo_png == b"team-logo-png"
    assert team.logo_card is None


# ----------------------------------------------------- view helpers


def wx_symbol_for(code: str) -> str:
    """Return *code*'s suit glyph (results_win's own map mirror)."""
    return {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}[code[-1]]


def _logo_text(code: str) -> str:
    """Return *code* as the view renders it (rank then suit glyph)."""
    return f"{code[:-1]}{wx_symbol_for(code)}"
