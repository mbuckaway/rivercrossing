# SPDX-License-Identifier: GPL-3.0-only
"""Functional tests for rider_editor_dlg live (E3.2): real Roster.

Drives the actual ``rider_editor_dlg`` XRC dialog wired to
``rivercrossing.ui.views.rider_editor.RiderEditor``, which now holds a
real, in-memory :class:`~rivercrossing.roster.Roster` and drives it
through :class:`~rivercrossing.ui.presenters.riders.RidersPresenter`
(E3.2.1/E3.2.2) -- the exemplar shape is ``test_selftest_dialog.py``'s
``_show``/try-finally pattern: never a mock in place of the real
Roster or the real dialog.

``_lists_common.demo_seeded_roster`` mirrors the E3.2-era
``rivercrossing.ui.app._seed_roster`` helper (moved to test code by
E5.4.2, since the bootstrap roster is now empty), so these tests
exercise the same seeded mixed roster shape production used to seed:
two solo entries and one pooled team, built from
``rivercrossing.demo``'s four fixture rows (E1.2.4).
``rivercrossing.demo`` is importable from tests (module docstring,
CLAUDE.md's removable-seam note) even though ``ui.views``/
``ui.presenters`` may never import it.
"""

import re
from typing import TYPE_CHECKING, Any

import harness
import pytest
import wx
import wx.dataview
from _lists_common import demo_seeded_roster

from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.ui import ids
from rivercrossing.ui.presenters.riders import SOLO_TEAM_CHOICE, CsvPreview
from rivercrossing.ui.views import dialogs
from rivercrossing.ui.views.rider_editor import (
    ADD_RIDER_INFOBAR,
    COL_TEAM,
    ROSTER_INFOBAR,
    RiderEditor,
)

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.functional

# The four seeded rows of the mixed demo roster (xrc-windows.md C),
# transcribed independently of demo.py and test_lists_demo.py (a
# mistake in one is caught by the other).
_SEEDED_ROWS = (
    ("123", "Sam Ellis", "solo"),
    ("77", "A. Roy", "Trail Blazers"),
    ("78", "K. Singh", "Trail Blazers"),
    ("212", "M. Chen", "solo"),
)

# test_csvio.py's own fixture home (its module docstring), reused here
# rather than re-derived -- test_app_bootstrap.py's mi_import_csv pins
# already exercise the identical file through the menu route.
_CLEAN_POOLED_FIXTURE = (
    Path(__file__).resolve().parents[1] / "unit" / "fixtures" / "csv" / "clean_pooled.csv"
)


def _show(xrc_resource: Any, roster: Roster) -> tuple[Any, RiderEditor]:  # noqa: ANN401
    """Load rider_editor_dlg, wire it live over *roster*, show, pump."""
    dialog = harness.load_window_verified(xrc_resource, ids.RIDER_EDITOR_DLG, frame=False)
    try:
        view = RiderEditor(dialog, roster=roster)
        dialog.Show()
        harness.pump()
    except Exception:  # Fault A: any post-load failure must close the dialog
        harness.close_window(dialog)
        raise
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


# --------------------------------------------------- roster_infobar


def test_rider_editor_dlg_infobar_disables_show_hide_effects(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """roster_infobar disables both slide effects (E3.2 follow-on).

    Measured (wxPython 4.3.1 / wxWidgets 3.3.3, macOS): ``Dismiss()``/
    ``ShowMessage()`` on a ``wx.InfoBar`` with its default slide
    effect never returns, shown or not -- ``_build_infobar``'s own
    docstring. ``test_console_demo.py``'s sibling pin covers the
    same fix on ``main_frame.py``'s three InfoBars.
    """
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        bar = harness.find_control(dialog, ROSTER_INFOBAR)
        effects = (bar.GetShowEffect(), bar.GetHideEffect())
    finally:
        harness.close_window(dialog)

    assert effects == (wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)


# ------------------------------------------------------------- opening


def test_rider_editor_dlg_opens_showing_the_seeded_roster_rows(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """xrc-windows.md C: Ellis, Roy/Singh (Trail Blazers), Chen."""
    roster = demo_seeded_roster()
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
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        plate_value = _plate_input_value(dialog)
    finally:
        harness.close_window(dialog)

    assert plate_value == "213"


def test_rider_editor_dlg_opens_populating_team_choice_with_solo_then_teams(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """team_choice: solo sentinel, then teams (no new-team item)."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        team_items = _team_choice_items(dialog)
    finally:
        harness.close_window(dialog)

    assert team_items == [SOLO_TEAM_CHOICE, "Trail Blazers"]


# ------------------------------------------------- W7 search + sort


def test_rider_editor_dlg_rider_search_filters_rows_by_name(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Typing in rider_search narrows riders_list to matching rows."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.type_text(dialog, ids.RIDER_SEARCH, "sam")
        rows = _rider_list_rows(dialog)
        harness.type_text(dialog, ids.RIDER_SEARCH, "")
        cleared_rows = _rider_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert rows == (("123", "Sam Ellis", "solo"),)
    assert cleared_rows == _SEEDED_ROWS


def test_rider_editor_dlg_rider_search_filters_rows_by_plate(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Search matches the Plate column as well as names (W7)."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.type_text(dialog, ids.RIDER_SEARCH, "77")
        rows = _rider_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert rows == (("77", "A. Roy", "Trail Blazers"),)


def test_rider_editor_dlg_rider_search_given_no_match_shows_an_empty_list(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A miss empties the list; the editor keeps working (W7)."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.type_text(dialog, ids.RIDER_SEARCH, "no-such-rider")
        rows = _rider_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert rows == ()


def test_rider_editor_dlg_riders_list_declares_the_three_sortable_headers(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """The three canvas columns exist; sorting is presenter-side (W7).

    Header clicks route to the presenter's own sort (the wxDataView
    index-list model cannot sort itself); the column header objects
    are asserted so a rename or reorder fails here, not in the VM.
    """
    roster = demo_seeded_roster()
    dialog, view = _show(xrc_resource, roster)

    try:
        headers = [view.riders_list.GetColumn(col).GetTitle() for col in range(3)]
    finally:
        harness.close_window(dialog)

    assert headers == ["Plate", "Name", "Team"]


# ------------------------------------------------------------------ add
# (W7: add_btn opens the dedicated add_rider_dlg; the editor's own
# form no longer adds. The dialog's ShowModal is driven through the
# run_add_rider_flow -> dialogs.run_dialog seam, monkeypatched to
# fill/click the real dialog then report how it ended, the same
# driver pattern the CSV-preview flow tests use.)


def test_rider_editor_dlg_add_btn_opens_the_add_dialog_prefilled_with_next_plate(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """add_btn opens add_rider_dlg; a cancelled Add changes nothing."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)
    found: dict[str, object] = {}

    def _drive_add(add_dialog: Any, _opener: Any) -> int:  # noqa: ANN401 -- wx ships no stubs
        found["plate"] = harness.find_control(add_dialog, ids.PLATE_INPUT).GetValue()
        harness.click(add_dialog, "wxID_CANCEL")
        return wx.ID_CANCEL

    monkeypatch.setattr(dialogs, "run_dialog", _drive_add)

    try:
        harness.click(dialog, ids.ADD_BTN)
        rows = _rider_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert found["plate"] == "213"
    assert rows == _SEEDED_ROWS


def test_rider_editor_dlg_add_btn_commits_a_new_rider_via_the_add_dialog(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding through the dialog appends a row and re-prefills."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    def _drive_add(add_dialog: Any, _opener: Any) -> int:  # noqa: ANN401 -- wx ships no stubs
        harness.type_text(add_dialog, ids.FIRST_NAME_INPUT, "New")
        harness.type_text(add_dialog, ids.LAST_NAME_INPUT, "Rider")
        harness.click(add_dialog, "wxID_OK")
        return wx.ID_OK

    monkeypatch.setattr(dialogs, "run_dialog", _drive_add)

    try:
        harness.click(dialog, ids.ADD_BTN)
        rows = _rider_list_rows(dialog)
        plate_value = _plate_input_value(dialog)
    finally:
        harness.close_window(dialog)

    assert rows[-1] == ("213", "New Rider", "solo")
    assert plate_value == "214"


def test_rider_editor_dlg_add_btn_blank_names_refuse_on_the_add_dialogs_infobar(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W7: Add with a blank name shows the dialog's infobar, no row."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)
    found: dict[str, object] = {}

    def _drive_add(add_dialog: Any, _opener: Any) -> int:  # noqa: ANN401 -- wx ships no stubs
        harness.click(add_dialog, "wxID_OK")
        found["infobar_shown"] = harness.find_control(add_dialog, ADD_RIDER_INFOBAR).IsShown()
        harness.click(add_dialog, "wxID_CANCEL")
        return wx.ID_CANCEL

    monkeypatch.setattr(dialogs, "run_dialog", _drive_add)

    try:
        harness.click(dialog, ids.ADD_BTN)
        rows = _rider_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert found["infobar_shown"] is True
    assert rows == _SEEDED_ROWS


def test_rider_editor_dlg_add_btn_duplicate_plate_refuses_on_the_add_dialogs_infobar(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A colliding plate refuses inside the dialog; the editor is whole."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)
    found: dict[str, object] = {}

    def _drive_add(add_dialog: Any, _opener: Any) -> int:  # noqa: ANN401 -- wx ships no stubs
        harness.type_text(add_dialog, ids.PLATE_INPUT, "77")
        harness.type_text(add_dialog, ids.FIRST_NAME_INPUT, "Dupe")
        harness.type_text(add_dialog, ids.LAST_NAME_INPUT, "Rider")
        harness.click(add_dialog, "wxID_OK")
        found["infobar_shown"] = harness.find_control(add_dialog, ADD_RIDER_INFOBAR).IsShown()
        harness.click(add_dialog, "wxID_CANCEL")
        return wx.ID_CANCEL

    monkeypatch.setattr(dialogs, "run_dialog", _drive_add)

    try:
        harness.click(dialog, ids.ADD_BTN)
        rows = _rider_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert found["infobar_shown"] is True
    assert rows == _SEEDED_ROWS


def test_rider_editor_dlg_successful_add_via_the_dialog_dismisses_a_prior_infobar(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The next successful action clears a prior editor warning (E3.2)."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    def _drive_add(add_dialog: Any, _opener: Any) -> int:  # noqa: ANN401 -- wx ships no stubs
        harness.type_text(add_dialog, ids.FIRST_NAME_INPUT, "Unique")
        harness.type_text(add_dialog, ids.LAST_NAME_INPUT, "Rider")
        harness.click(add_dialog, "wxID_OK")
        return wx.ID_OK

    monkeypatch.setattr(dialogs, "run_dialog", _drive_add)

    try:
        # A refused Save (colliding plate) puts the editor's own
        # warning up first.
        harness.select_row(dialog, ids.RIDERS_LIST, 0)
        harness.type_text(dialog, ids.PLATE_INPUT, "77")
        harness.type_text(dialog, ids.FIRST_NAME_INPUT, "Sam")
        harness.type_text(dialog, ids.LAST_NAME_INPUT, "Ellis")
        harness.click(dialog, ids.SAVE_BTN)
        refused_shown = harness.find_control(dialog, ROSTER_INFOBAR).IsShown()
        harness.click(dialog, ids.ADD_BTN)
        infobar_shown = harness.find_control(dialog, ROSTER_INFOBAR).IsShown()
    finally:
        harness.close_window(dialog)

    assert refused_shown is True
    assert infobar_shown is False


# ----------------------------------------------------- save gating (W7)


def test_rider_editor_dlg_save_btn_enabled_only_while_the_form_differs(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """W7: a clean record disables Save; edits enable it; reverting not.

    A clean form means Enter is a no-op (its default button is
    disabled), which is what closes the plate-1-disappears trap:
    Save can never fire over a record the operator did not change.
    """
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.select_row(dialog, ids.RIDERS_LIST, 0)
        clean_enabled = harness.find_control(dialog, ids.SAVE_BTN).IsEnabled()
        harness.type_text(dialog, ids.FIRST_NAME_INPUT, "Samuel")
        dirty_enabled = harness.find_control(dialog, ids.SAVE_BTN).IsEnabled()
        harness.type_text(dialog, ids.FIRST_NAME_INPUT, "Sam")
        reverted_enabled = harness.find_control(dialog, ids.SAVE_BTN).IsEnabled()
    finally:
        harness.close_window(dialog)

    assert clean_enabled is False
    assert dirty_enabled is True
    assert reverted_enabled is False


# ----------------------------------------------------------------- save


def test_rider_editor_dlg_save_updates_the_selected_rows_name(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Select a row, edit Name, Save -- that row updates (R-20)."""
    roster = demo_seeded_roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.select_row(dialog, ids.RIDERS_LIST, 0)
        harness.type_text(dialog, ids.FIRST_NAME_INPUT, "Samuel")
        harness.type_text(dialog, ids.LAST_NAME_INPUT, "Ellis")
        harness.click(dialog, ids.SAVE_BTN)
        rows = _rider_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert rows[0] == ("123", "Samuel Ellis", "solo")


# --------------------------------------------------------------- delete


def test_rider_editor_dlg_delete_removes_the_selected_draft_entry(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Delete on a DRAFT entry with no data removes its row (R-15)."""
    roster = demo_seeded_roster()
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
    roster = demo_seeded_roster()
    roster.mark_has_data(roster.entries[0])
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.select_row(dialog, ids.RIDERS_LIST, 0)
        delete_enabled = harness.find_control(dialog, ids.DELETE_BTN).IsEnabled()
    finally:
        harness.close_window(dialog)

    assert delete_enabled is False


def test_rider_editor_dlg_deleting_the_only_entry_empties_the_list_and_choice(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """T-4: show_riders([]) and the bare solo-sentinel team_choice.

    A single-entry roster's own delete drives ``show_riders`` to an
    empty ``riders_list`` and ``show_team_choices`` to its smallest
    real content (no teams at all) -- proven at the view, not only
    at the presenter (``test_riders.py``'s own empty-roster proof).
    """
    roster = Roster()
    roster.create_solo_entry(first_name="Solo", last_name="One", plate="1")
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.select_row(dialog, ids.RIDERS_LIST, 0)
        harness.click(dialog, ids.DELETE_BTN)
        rows = _rider_list_rows(dialog)
        team_items = _team_choice_items(dialog)
    finally:
        harness.close_window(dialog)

    assert rows == ()
    assert team_items == [SOLO_TEAM_CHOICE]


def test_rider_editor_dlg_stale_row_selection_event_is_a_safe_no_op(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A selection-changed event with nothing selected is a no-op.

    Selects the seeded roster's only entry, then deletes it --
    ``show_riders`` associates a fresh, empty model, which carries no
    selection forward (measured), so ``riders_list.GetSelection()``
    is genuinely invalid afterwards. Posting a second selection-
    changed event in that state must not crash ``_on_row_selected``'s
    own ``item.IsOk()`` guard, and must leave the (still empty) list
    exactly as the delete left it.
    """
    roster = Roster()
    roster.create_solo_entry(first_name="Solo", last_name="One", plate="1")
    dialog, view = _show(xrc_resource, roster)

    try:
        harness.select_row(dialog, ids.RIDERS_LIST, 0)
        harness.click(dialog, ids.DELETE_BTN)
        stale_event = wx.dataview.DataViewEvent(
            wx.dataview.wxEVT_DATAVIEW_SELECTION_CHANGED,
            view.riders_list,
            wx.dataview.NullDataViewItem,
        )
        view.riders_list.GetEventHandler().ProcessEvent(stale_event)
        harness.pump()
        rows = _rider_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert rows == ()


# --------------------------------------------------- preselect seam
# (topic/ux-polish: the console Riders tab's bootstrap calls
# ``RiderEditor.select_rider_by_plate`` after opening the editor so
# the operator lands on an existing rider's form, not the add form.)


def _split_name_mixed_roster() -> Roster:
    """Return a mixed, pooled roster with split first/last names."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Sam", last_name="Ellis", plate="123")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    return roster


def test_rider_editor_dlg_select_rider_by_plate_selects_the_row_and_fills_the_form(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A matching plate selects its riders_list row and fills the form.

    The console Riders tab preselect seam: the bootstrap calls this
    after opening the editor so the operator lands on an existing
    rider. The form fills through the presenter's own
    ``on_row_selected`` -- never by relying on the programmatic
    ``Select`` event alone (``harness.select_row``'s measured note:
    MSW's native DataViewCtrl fires no selection event for it).
    """
    roster = _split_name_mixed_roster()
    dialog, view = _show(xrc_resource, roster)

    try:
        view.select_rider_by_plate("77")
        harness.pump()
        model = view.riders_list.GetModel()
        selected_row = model.GetRow(view.riders_list.GetSelection())
        plate = _plate_input_value(dialog)
        first = harness.find_control(dialog, ids.FIRST_NAME_INPUT).GetValue()
        last = harness.find_control(dialog, ids.LAST_NAME_INPUT).GetValue()
        team = harness.find_control(dialog, ids.TEAM_CHOICE).GetStringSelection()
    finally:
        harness.close_window(dialog)

    assert (selected_row, plate, first, last, team) == (
        1,
        "77",
        "A.",
        "Roy",
        "Trail Blazers",
    )


def test_rider_editor_dlg_select_rider_by_plate_given_an_unknown_plate_is_a_no_op(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A plate with no riders_list row leaves everything untouched."""
    roster = _split_name_mixed_roster()
    dialog, view = _show(xrc_resource, roster)

    try:
        before = (
            _plate_input_value(dialog),
            harness.find_control(dialog, ids.FIRST_NAME_INPUT).GetValue(),
            harness.find_control(dialog, ids.LAST_NAME_INPUT).GetValue(),
        )
        view.select_rider_by_plate("999")
        harness.pump()
        selection_ok = view.riders_list.GetSelection().IsOk()
        after = (
            _plate_input_value(dialog),
            harness.find_control(dialog, ids.FIRST_NAME_INPUT).GetValue(),
            harness.find_control(dialog, ids.LAST_NAME_INPUT).GetValue(),
        )
    finally:
        harness.close_window(dialog)

    assert selection_ok is False
    assert after == before


# -------------------------------------------------- solo/mixed variant


def _solo_only_roster() -> Roster:
    """Return a bare, solo-only roster (E3.4.2's own "solo" case)."""
    roster = Roster()
    roster.create_solo_entry(first_name="Solo", last_name="One", plate="1")
    return roster


def _mixed_roster() -> Roster:
    """Return the seeded mixed roster (E3.4.2's own "mixed" case)."""
    return demo_seeded_roster()


@pytest.mark.parametrize(
    ("roster_factory", "expected_visible"),
    [(_solo_only_roster, False), (_mixed_roster, True)],
    ids=["solo_only", "mixed"],
)
def test_rider_editor_dlg_team_ui_visibility_matches_entry_mode(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    roster_factory: Callable[[], Roster],
    expected_visible: bool,  # noqa: FBT001 -- a parametrize row's value, not a call-site bool
) -> None:
    """R-11: Team column + team_choice visibility follows entry_mode.

    E3.4.2's own "both states" harness assertion: solo-only hides
    both; mixed shows both -- the editor's other flows (add/save/
    delete) already run against the mixed roster throughout this
    file's earlier tests, so "still work in mixed" is proven there,
    not repeated here.
    """
    dialog, view = _show(xrc_resource, roster_factory())

    try:
        team_choice_shown = harness.find_control(dialog, ids.TEAM_CHOICE).IsShown()
        column_hidden = view.riders_list.GetColumn(COL_TEAM).IsHidden()
    finally:
        harness.close_window(dialog)

    assert team_choice_shown is expected_visible
    assert column_hidden is not expected_visible


# --------------------------------------------------- csv_preview_dlg
# (E3.4's own scope; these members exist only to satisfy RidersView.)

def test_rider_editor_dlg_show_csv_preview_raises_not_implemented_naming_e3_4(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """T-5: show_csv_preview's only raise, naming its follow-up."""
    roster = demo_seeded_roster()
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
    roster = demo_seeded_roster()
    dialog, view = _show(xrc_resource, roster)

    try:
        with pytest.raises(NotImplementedError, match=re.escape("E3.4")):
            view.set_import_enabled(enabled=True)
    finally:
        harness.close_window(dialog)


# ------------------------------- Fault A: the load-construct seam
# (hosted-runner red, deterministic here: view construction is forced
# to raise between the load and the caller's try/finally, and the
# just-loaded dialog must not be left fully alive -- see _show's guard.)


def test_show_closes_the_dialog_when_view_construction_raises(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fault A red: a post-load failure must not leak the dialog.

    ``_show`` loads the dialog, constructs ``RiderEditor`` (whose
    ``_find`` -> ``ui.views._support.find_control`` can exhaust its 25
    retries and raise a ``LookupError`` under hosted-runner load),
    shows and pumps -- all before the test's own ``try/finally``. The
    just-loaded dialog then leaks fully alive (``is_being_deleted=
    False``), is rerun-masked by ``--reruns 2``, and later trips the
    reap pin. ``find_control`` is forced to raise here so the leak is
    reproduced deterministically: red until ``_show`` closes the
    dialog on the way out.
    """
    roster = demo_seeded_roster()

    def _find_that_raises(*_args: Any, **_kwargs: Any) -> Any:  # noqa: ANN401
        raise LookupError("simulated find_control failure")

    monkeypatch.setattr(rider_editor, "find_control", _find_that_raises)

    with pytest.raises(LookupError, match=re.escape("simulated find_control failure")):
        _show(xrc_resource, roster)

    assert wx.Window.FindWindowByName(ids.RIDER_EDITOR_DLG) is None


def test_run_csv_import_flow_closes_the_dialog_when_view_construction_raises(
    xrc_resource: object,  # noqa: ARG001 -- ordering only, see conftest
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fault A red: construction failure must not leak the dialog.

    ``run_csv_import_flow`` loads ``csv_preview_dlg`` straight off the
    process-global ``wx.xrc.XmlResource`` and constructs
    ``CsvPreviewDialog`` (whose ``_find`` can exhaust its 25 retries
    under hosted-runner load) *before* the ``try/finally`` that
    destroys it -- a post-load raise leaks the dialog fully alive, is
    rerun-masked by ``--reruns 2``, and later trips the reap pin.
    ``CsvPreviewDialog`` is forced to raise here so the leak is
    reproduced deterministically: red until the flow closes the dialog
    on the way out.
    """
    roster = demo_seeded_roster()
    monkeypatch.setattr(rider_editor, "_pick_import_path", lambda _parent: _CLEAN_POOLED_FIXTURE)

    def _construction_that_raises(*_args: Any, **_kwargs: Any) -> Any:  # noqa: ANN401
        raise LookupError("simulated CsvPreviewDialog construction failure")

    monkeypatch.setattr(rider_editor, "CsvPreviewDialog", _construction_that_raises)

    parent = wx.Frame(None)
    try:
        with pytest.raises(
            LookupError, match=re.escape("simulated CsvPreviewDialog construction failure")
        ):
            rider_editor.run_csv_import_flow(parent, roster)
    finally:
        harness.close_window(parent)

    assert wx.Window.FindWindowByName(ids.CSV_PREVIEW_DLG) is None
