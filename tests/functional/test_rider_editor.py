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
from pathlib import Path
from typing import TYPE_CHECKING, Any

import harness
import pytest
import wx
import wx.dataview

from rivercrossing import csvio
from rivercrossing.demo import DemoDataSource
from rivercrossing.roster import Roster
from rivercrossing.ui import ids
from rivercrossing.ui.app import _seed_roster
from rivercrossing.ui.presenters.riders import NEW_TEAM_CHOICE, SOLO_TEAM_CHOICE, CsvPreview
from rivercrossing.ui.views import dialogs, rider_editor
from rivercrossing.ui.views.rider_editor import COL_TEAM, ROSTER_INFOBAR, RiderEditor

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.functional

# xrc-windows.md C, transcribed independently of demo.py and
# test_lists_demo.py (a mistake in one is caught by the other).
_SEEDED_ROWS = (
    ("123", "Sam Ellis", "—"),
    ("77", "A. Roy", "Trail Blazers"),
    ("78", "K. Singh", "Trail Blazers"),
    ("212", "M. Chen", "—"),
)

# test_csvio.py's own fixture home (its module docstring), reused here
# rather than re-derived -- test_app_bootstrap.py's mi_import_csv pins
# already exercise the identical file through the menu route.
_CLEAN_POOLED_FIXTURE = (
    Path(__file__).resolve().parents[1] / "unit" / "fixtures" / "csv" / "clean_pooled.csv"
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
    roster = _seed_roster(DemoDataSource())
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


def test_rider_editor_dlg_deleting_the_only_entry_empties_the_list_and_choice(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """T-4: show_riders([]) and the bare two-sentinel team_choice.

    A single-entry roster's own delete drives ``show_riders`` to an
    empty ``riders_list`` and ``show_team_choices`` to its smallest
    real content (no teams at all) -- proven at the view, not only
    at the presenter (``test_riders.py``'s own empty-roster proof).
    """
    roster = Roster()
    roster.create_solo_entry(name="Solo One", plate="1")
    dialog, _view = _show(xrc_resource, roster)

    try:
        harness.select_row(dialog, ids.RIDERS_LIST, 0)
        harness.click(dialog, ids.DELETE_BTN)
        rows = _rider_list_rows(dialog)
        team_items = _team_choice_items(dialog)
    finally:
        harness.close_window(dialog)

    assert rows == ()
    assert team_items == [SOLO_TEAM_CHOICE, NEW_TEAM_CHOICE]


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
    roster.create_solo_entry(name="Solo One", plate="1")
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


# -------------------------------------------------- solo/mixed variant


def _solo_only_roster() -> Roster:
    """Return a bare, solo-only roster (E3.4.2's own "solo" case)."""
    roster = Roster()
    roster.create_solo_entry(name="Solo One", plate="1")
    return roster


def _mixed_roster() -> Roster:
    """Return the seeded mixed roster (E3.4.2's own "mixed" case)."""
    return _seed_roster(DemoDataSource())


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
    delete/new-team) already run against the mixed roster throughout
    this file's earlier tests, so "still work in mixed" is proven
    there, not repeated here.
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


# --------------------------------------------- editor's own csv buttons
# (E3.4's own follow-on, R-73: every frozen control must be drivable,
# not only the two menu routes.)


def test_rider_editor_dlg_import_btn_with_a_clean_fixture_adds_its_rows(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """import_btn runs the identical picker->preview->commit flow.

    ``rider_editor.run_csv_import_flow`` is mi_import_csv's own route
    handler's one call too (test_app_bootstrap.py's own pins) -- this
    proves the *other* caller: clicking "Import CSV…" commits into
    the same roster this still-open editor reads, then re-renders its
    own rows from it, exactly as if it had just been reopened.
    """
    roster = _seed_roster(DemoDataSource())
    dialog, _view = _show(xrc_resource, roster)
    monkeypatch.setattr(rider_editor, "_pick_import_path", lambda _parent: _CLEAN_POOLED_FIXTURE)

    def _click_import(preview_dialog: Any, opener: Any) -> int:  # noqa: ANN401, ARG001
        harness.click(preview_dialog, "wxID_OK")
        return wx.ID_OK

    monkeypatch.setattr(dialogs, "run_dialog", _click_import)

    try:
        harness.click(dialog, ids.IMPORT_BTN)
        rows = _rider_list_rows(dialog)
    finally:
        harness.close_window(dialog)

    assert ("1", "Alex Ferreira", "—") in rows
    assert len(rows) == len(_SEEDED_ROWS) + 9


def test_rider_editor_dlg_import_btn_given_a_cancelled_picker_is_a_no_op(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """task-briefs.md's own "cancelled picker = no dialog" (E3.4)."""
    roster = _seed_roster(DemoDataSource())
    dialog, _view = _show(xrc_resource, roster)
    monkeypatch.setattr(rider_editor, "_pick_import_path", lambda _parent: None)
    before = len(wx.GetTopLevelWindows())

    try:
        harness.click(dialog, ids.IMPORT_BTN)
        rows = _rider_list_rows(dialog)
        after = len(wx.GetTopLevelWindows())
    finally:
        harness.close_window(dialog)

    assert rows == _SEEDED_ROWS
    assert after == before


def test_rider_editor_dlg_export_btn_writes_a_file_that_repreviews_clean(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """export_btn runs the identical picker->write flow (E3.4).

    ``rider_editor.run_csv_export_flow`` is mi_export_csv's own route
    handler's one call too -- re-previewing the written file against
    a fresh roster (never the one just exported) proves the file's
    own header/rows round-trip clean, not only that a file exists.
    """
    roster = _seed_roster(DemoDataSource())
    dialog, _view = _show(xrc_resource, roster)
    export_path = tmp_path / "export.csv"
    monkeypatch.setattr(rider_editor, "_pick_export_path", lambda _parent: export_path)

    try:
        harness.click(dialog, ids.EXPORT_BTN)
        preview = csvio.preview(export_path, Roster())
    finally:
        harness.close_window(dialog)

    assert export_path.exists()
    assert preview.conflicts == ()


def test_rider_editor_dlg_export_btn_given_a_cancelled_picker_is_a_no_op(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A cancelled save picker writes nothing, silently (E3.4)."""
    roster = _seed_roster(DemoDataSource())
    dialog, _view = _show(xrc_resource, roster)
    export_path = tmp_path / "export.csv"
    monkeypatch.setattr(rider_editor, "_pick_export_path", lambda _parent: None)

    try:
        harness.click(dialog, ids.EXPORT_BTN)
    finally:
        harness.close_window(dialog)

    assert export_path.exists() is False
