# SPDX-License-Identifier: GPL-3.0-only
"""Functional tests for rider_editor_dlg live (E3.2): real Roster.

Drives the actual ``rider_editor_dlg`` XRC dialog wired to
``rivercrossing.ui.views.rider_editor.RiderEditor``, which now holds a
real, in-memory :class:`~rivercrossing.roster.Roster` and drives it
through :class:`~rivercrossing.ui.presenters.riders.RidersPresenter`
(E3.2.1/E3.2.2) -- the exemplar shape is ``test_selftest_dialog.py``'s
``_show``/try-finally pattern: never a mock in place of the real
Roster or the real dialog.

``_seed_roster`` mirrors ``rivercrossing.ui.app``'s own bootstrap
helper exactly (imported from there rather than re-derived here), so
these tests exercise the identical roster shape production actually
seeds: two solo entries and one pooled team, built from
``rivercrossing.demo``'s four fixture rows (E1.2.4).
``rivercrossing.demo`` is importable from tests (module docstring,
CLAUDE.md's removable-seam note) even though ``ui.views``/
``ui.presenters`` may never import it.
"""

import re
from typing import Any

import harness
import pytest

from rivercrossing.demo import DemoDataSource
from rivercrossing.roster import Roster
from rivercrossing.ui import ids
from rivercrossing.ui.app import _seed_roster
from rivercrossing.ui.presenters.riders import NEW_TEAM_CHOICE, SOLO_TEAM_CHOICE, CsvPreview
from rivercrossing.ui.views.rider_editor import COL_TEAM, ROSTER_INFOBAR, RiderEditor

pytestmark = pytest.mark.functional

# xrc-windows.md C, transcribed independently of demo.py and
# test_lists_demo.py (a mistake in one is caught by the other).
_SEEDED_ROWS = (
    ("123", "Sam Ellis", "—"),
    ("77", "A. Roy", "Trail Blazers"),
    ("78", "K. Singh", "Trail Blazers"),
    ("212", "M. Chen", "—"),
)


def _show(xrc_resource: Any, roster: Roster) -> tuple[Any, RiderEditor]:  # noqa: ANN401
    """Load rider_editor_dlg, wire it live over *roster*, show, pump."""
    dialog = harness.load_window(xrc_resource, ids.RIDER_EDITOR_DLG, frame=False)
    view = RiderEditor(dialog, roster=roster)
    dialog.Show()
    harness.pump()
    return dialog, view


def _rider_list_rows(dialog: Any) -> tuple[tuple[str, str, str], ...]:  # noqa: ANN401
    """Return every riders_list row as (plate, name, team) text."""
    model = harness.find_control(dialog, ids.RIDERS_LIST).GetModel()
    return tuple(
        tuple(model.GetValueByRow(row, col) for col in range(3)) for row in range(model.GetCount())
    )


def _team_choice_items(dialog: Any) -> list[str]:  # noqa: ANN401 -- wx ships no stubs
    """Return team_choice's current content, in order."""
    choice = harness.find_control(dialog, ids.TEAM_CHOICE)
    return [choice.GetString(i) for i in range(choice.GetCount())]


def _plate_input_value(dialog: Any) -> str:  # noqa: ANN401 -- wx ships no stubs
    """Return plate_input's current text."""
    return harness.find_control(dialog, ids.PLATE_INPUT).GetValue()


# ------------------------------------------------------------- opening


def test_rider_editor_dlg_opens_showing_the_seeded_roster_rows(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """xrc-windows.md C: Ellis, Roy/Singh (Trail Blazers), Chen."""
    roster = _seed_roster(DemoDataSource())
    dialog, _view = _show(xrc_resource, roster)

    try:
        rows = _rider_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert rows == _SEEDED_ROWS


def test_rider_editor_dlg_opens_prefilling_the_next_free_plate(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """R-20: plate_input starts one past the highest plate in use."""
    roster = _seed_roster(DemoDataSource())
    dialog, _view = _show(xrc_resource, roster)

    try:
        plate_value = _plate_input_value(dialog)
    finally:
        harness.close_window(dialog)

    assert plate_value == "213"


def test_rider_editor_dlg_opens_populating_team_choice_with_solo_and_new_team(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """team_choice: solo sentinel, every team, new-team sentinel."""
    roster = _seed_roster(DemoDataSource())
    dialog, _view = _show(xrc_resource, roster)

    try:
        team_items = _team_choice_items(dialog)
    finally:
        harness.close_window(dialog)

    assert team_items == [SOLO_TEAM_CHOICE, "Trail Blazers", NEW_TEAM_CHOICE]


# ------------------------------------------------------------------ add


def test_rider_editor_dlg_add_creates_a_solo_entry_and_reprefills_the_plate(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Add with the default (solo) team appends a row, re-prefills."""
    roster = _seed_roster(DemoDataSource())
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.type_text(dialog, ids.NAME_INPUT, "New Rider")
        harness.click(dialog, ids.ADD_BTN)
        rows = _rider_list_rows(dialog)
        plate_value = _plate_input_value(dialog)
    finally:
        harness.close_window(dialog)

    assert rows[-1] == ("213", "New Rider", "—")
    assert plate_value == "214"


def test_rider_editor_dlg_add_duplicate_plate_shows_the_infobar_without_crashing(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A colliding plate refuses via roster_infobar, not a crash."""
    roster = _seed_roster(DemoDataSource())
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.type_text(dialog, ids.PLATE_INPUT, "77")
        harness.type_text(dialog, ids.NAME_INPUT, "Dupe Rider")
        harness.click(dialog, ids.ADD_BTN)
        infobar_shown = harness.find_control(dialog, ROSTER_INFOBAR).IsShown()
        row_count = harness.find_control(dialog, ids.RIDERS_LIST).GetModel().GetCount()
    finally:
        harness.close_window(dialog)

    assert infobar_shown is True
    assert row_count == len(_SEEDED_ROWS)


def test_rider_editor_dlg_successful_add_dismisses_a_prior_infobar(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """The next successful action clears a prior warning (E3.2)."""
    roster = _seed_roster(DemoDataSource())
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.type_text(dialog, ids.PLATE_INPUT, "77")
        harness.type_text(dialog, ids.NAME_INPUT, "Dupe Rider")
        harness.click(dialog, ids.ADD_BTN)
        harness.type_text(dialog, ids.PLATE_INPUT, "999")
        harness.type_text(dialog, ids.NAME_INPUT, "Unique Rider")
        harness.click(dialog, ids.ADD_BTN)
        infobar_shown = harness.find_control(dialog, ROSTER_INFOBAR).IsShown()
    finally:
        harness.close_window(dialog)

    assert infobar_shown is False


# ----------------------------------------------------------------- save


def test_rider_editor_dlg_save_updates_the_selected_rows_name(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Select a row, edit Name, Save -- that row updates (R-20)."""
    roster = _seed_roster(DemoDataSource())
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.select_row(dialog, ids.RIDERS_LIST, 0)
        harness.type_text(dialog, ids.NAME_INPUT, "Samuel Ellis")
        harness.click(dialog, ids.SAVE_BTN)
        rows = _rider_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert rows[0] == ("123", "Samuel Ellis", "—")


# --------------------------------------------------------------- delete


def test_rider_editor_dlg_delete_removes_the_selected_draft_entry(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Delete on a DRAFT entry with no data removes its row (R-15)."""
    roster = _seed_roster(DemoDataSource())
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.select_row(dialog, ids.RIDERS_LIST, 0)
        harness.click(dialog, ids.DELETE_BTN)
        rows = _rider_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert rows == _SEEDED_ROWS[1:]


def test_rider_editor_dlg_delete_btn_disabled_once_the_entry_has_data(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """delete_btn tracks the presenter's has-data guard (R-15)."""
    roster = _seed_roster(DemoDataSource())
    roster.mark_has_data(roster.entries[0])
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.select_row(dialog, ids.RIDERS_LIST, 0)
        delete_enabled = harness.find_control(dialog, ids.DELETE_BTN).IsEnabled()
    finally:
        harness.close_window(dialog)

    assert delete_enabled is False


# ------------------------------------------------------------- new team


def test_rider_editor_dlg_new_team_flow_creates_the_team_and_shows_it_in_the_row(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The New team sentinel, then Add, creates a team (R-20).

    The native ``wx.TextEntryDialog`` prompt is never driven: the
    view's own seam method is monkeypatched instead, following
    ``test_selftest_dialog.py``'s precedent for a monkeypatched-seam
    proof over a native modal.
    """
    roster = _seed_roster(DemoDataSource())
    dialog, view = _show(xrc_resource, roster)
    monkeypatch.setattr(view, "prompt_new_team_name", lambda: "Dirt Dynamos")

    try:
        harness.type_text(dialog, ids.NAME_INPUT, "J. Park")
        harness.select_choice(dialog, ids.TEAM_CHOICE, NEW_TEAM_CHOICE)
        harness.click(dialog, ids.ADD_BTN)
        team_items = _team_choice_items(dialog)
        rows = _rider_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert "Dirt Dynamos" in team_items
    assert rows[-1] == ("213", "J. Park", "Dirt Dynamos")


def test_rider_editor_dlg_new_team_flow_cancelled_creates_no_entry(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling the native prompt (None) is a no-op (R-20)."""
    roster = _seed_roster(DemoDataSource())
    dialog, view = _show(xrc_resource, roster)
    monkeypatch.setattr(view, "prompt_new_team_name", lambda: None)

    try:
        harness.type_text(dialog, ids.NAME_INPUT, "J. Park")
        harness.select_choice(dialog, ids.TEAM_CHOICE, NEW_TEAM_CHOICE)
        harness.click(dialog, ids.ADD_BTN)
        rows = _rider_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert rows == _SEEDED_ROWS


# ------------------------------------------------------------ solo-only


def test_rider_editor_dlg_solo_only_roster_hides_the_team_column_and_choice(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """R-11: a solo-only roster hides team_choice and Team column."""
    roster = Roster()
    roster.create_solo_entry(name="Solo One", plate="1")
    dialog, view = _show(xrc_resource, roster)

    try:
        team_choice_shown = harness.find_control(dialog, ids.TEAM_CHOICE).IsShown()
        column_hidden = view.riders_list.GetColumn(COL_TEAM).IsHidden()
    finally:
        harness.close_window(dialog)

    assert team_choice_shown is False
    assert column_hidden is True


# --------------------------------------------------- csv_preview_dlg
# (E3.4's own scope; these members exist only to satisfy RidersView.)


def test_rider_editor_dlg_show_csv_preview_raises_not_implemented_naming_e3_4(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """T-5: show_csv_preview's only raise, naming its follow-up."""
    roster = _seed_roster(DemoDataSource())
    dialog, view = _show(xrc_resource, roster)

    try:
        with pytest.raises(NotImplementedError, match=re.escape("E3.4")):
            view.show_csv_preview(CsvPreview(summary="", conflicts=()))
    finally:
        harness.close_window(dialog)


def test_rider_editor_dlg_set_import_enabled_raises_not_implemented_naming_e3_4(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """T-5: set_import_enabled's only raise, naming its follow-up."""
    roster = _seed_roster(DemoDataSource())
    dialog, view = _show(xrc_resource, roster)

    try:
        with pytest.raises(NotImplementedError, match=re.escape("E3.4")):
            view.set_import_enabled(enabled=True)
    finally:
        harness.close_window(dialog)
