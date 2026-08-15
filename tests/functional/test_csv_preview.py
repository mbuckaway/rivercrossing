# SPDX-License-Identifier: GPL-3.0-only
"""Functional tests for csv_preview_dlg live (E3.4): preview + commit.

Drives ``rivercrossing.ui.views.rider_editor.CsvPreviewDialog``
directly (never through ``app.py``'s menu route -- that path has its
own dedicated pins in ``test_app_bootstrap.py``), over a real, in-
memory :class:`~rivercrossing.roster.Roster` and real ``tmp_path``
CSV files: never a mock in place of the real roster or the real
dialog, following ``test_rider_editor.py``'s own precedent.
"""

import re
from typing import Any

import harness
import pytest
import wx

from rivercrossing.roster import Roster
from rivercrossing.ui import ids
from rivercrossing.ui.views import rider_editor
from rivercrossing.ui.views.rider_editor import CSV_INFOBAR, CsvPreviewDialog

pytestmark = pytest.mark.functional


def _write_pooled_csv(directory: Any, rows: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Write a minimal rider_pooled CSV fixture; return its path."""
    path = directory / "riders.csv"
    path.write_text(f"plate,name,team_name,notes\n{rows}", encoding="utf-8")
    return path


def _show(xrc_resource: Any, roster: Roster) -> tuple[Any, CsvPreviewDialog]:  # noqa: ANN401
    """Load csv_preview_dlg, wire it live over *roster*, show, pump."""
    dialog = harness.load_window(xrc_resource, ids.CSV_PREVIEW_DLG, frame=False)
    try:
        view = CsvPreviewDialog(dialog, roster=roster)
        dialog.Show()
        harness.pump()
    except Exception:  # Fault A: any post-load failure must close the dialog
        harness.close_window(dialog)
        raise
    return dialog, view


def _conflict_rows(dialog: Any) -> tuple[tuple[str, str], ...]:  # noqa: ANN401
    """Return every conflicts_list row as (row, problem) text."""
    model = harness.find_control(dialog, ids.CONFLICTS_LIST).GetModel()
    return tuple(
        tuple(model.GetValueByRow(row, col) for col in range(2)) for row in range(model.GetCount())
    )


# ------------------------------------------------------------- preview


def test_csv_preview_dlg_conflicted_file_shows_the_exact_rows_and_disables_import(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    tmp_path: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """xrc-windows.md C: summary_lbl + Row|Problem, wxID_OK disabled."""
    roster = Roster()
    dialog, view = _show(xrc_resource, roster)
    path = _write_pooled_csv(tmp_path, "1,Alex One,,\n1,Bo Two,,\n")

    try:
        view.presenter.on_pick_csv_import(path)
        summary = view.summary_lbl.GetLabelText()
        rows = _conflict_rows(dialog)
        import_enabled = view.ok_btn.IsEnabled()
    finally:
        harness.close_window(dialog)

    assert summary == "riders.csv → 2 riders · 0 teams · 1 conflicts"
    assert rows == (("3", "duplicate plate 1"),)
    assert import_enabled is False


def test_csv_preview_dlg_clean_file_shows_the_exact_summary_and_enables_import(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    tmp_path: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A conflict-free file enables the stock wxID_OK "Import"."""
    roster = Roster()
    dialog, view = _show(xrc_resource, roster)
    path = _write_pooled_csv(tmp_path, "1,Alex One,,\n2,Bo Two,,\n")

    try:
        view.presenter.on_pick_csv_import(path)
        summary = view.summary_lbl.GetLabelText()
        import_enabled = view.ok_btn.IsEnabled()
    finally:
        harness.close_window(dialog)

    assert summary == "riders.csv → 2 riders · 0 teams · 0 conflicts"
    assert import_enabled is True


# -------------------------------------------------------------- import


def test_csv_preview_dlg_import_click_commits_into_the_roster(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    tmp_path: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Clicking "Import" (wxID_OK) applies the preview (R-21)."""
    roster = Roster()
    dialog, view = _show(xrc_resource, roster)
    path = _write_pooled_csv(tmp_path, "1,Alex One,,\n2,Bo Two,,\n")
    view.presenter.on_pick_csv_import(path)

    try:
        harness.click(dialog, "wxID_OK")
        names = [entry.display_name for entry in roster.entries]
    finally:
        harness.close_window(dialog)

    assert names == ["Alex One", "Bo Two"]


def test_csv_preview_dlg_import_click_closes_the_dialog(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    tmp_path: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A successful Import ends the dialog with wxID_OK (§13)."""
    roster = Roster()
    dialog, view = _show(xrc_resource, roster)
    path = _write_pooled_csv(tmp_path, "1,Alex One,,\n")
    view.presenter.on_pick_csv_import(path)

    try:
        harness.click(dialog, "wxID_OK")
        shown_after = dialog.IsShown()
        return_code = dialog.GetReturnCode()
    finally:
        harness.close_window(dialog)

    assert shown_after is False
    assert return_code == wx.ID_OK


def test_csv_preview_dlg_import_disabled_by_conflicts_leaves_the_dialog_open(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    tmp_path: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A bypassed disabled Import still refuses, showing why (E3.4).

    Direct event injection posts the click regardless of
    ``IsEnabled()`` (harness.py's own module docstring measures this
    for every button) -- proving the *handler* itself refuses,
    matching a real click, which the OS would never deliver to a
    disabled button in the first place.
    """
    roster = Roster()
    dialog, view = _show(xrc_resource, roster)
    path = _write_pooled_csv(tmp_path, "1,Alex One,,\n1,Bo Two,,\n")
    view.presenter.on_pick_csv_import(path)

    try:
        harness.click(dialog, "wxID_OK")
        shown_after = dialog.IsShown()
        infobar_shown = harness.find_control(dialog, CSV_INFOBAR).IsShown()
        entries = roster.entries
    finally:
        harness.close_window(dialog)

    assert shown_after is True
    assert infobar_shown is True
    assert entries == ()


# -------------------------------------------------------------- cancel


def test_csv_preview_dlg_cancel_click_writes_nothing(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    tmp_path: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Cancel leaves the roster exactly as it was (spec.md §7)."""
    roster = Roster()
    roster.create_solo_entry(name="Existing Rider", plate="9")
    dialog, view = _show(xrc_resource, roster)
    path = _write_pooled_csv(tmp_path, "1,Alex One,,\n2,Bo Two,,\n")
    view.presenter.on_pick_csv_import(path)

    try:
        harness.click(dialog, "wxID_CANCEL")
        names = [entry.display_name for entry in roster.entries]
    finally:
        harness.close_window(dialog)

    assert names == ["Existing Rider"]


def test_csv_preview_dlg_cancel_click_closes_the_dialog(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    tmp_path: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Cancel ends the dialog too, via wx's own native handling."""
    roster = Roster()
    dialog, view = _show(xrc_resource, roster)
    path = _write_pooled_csv(tmp_path, "1,Alex One,,\n")
    view.presenter.on_pick_csv_import(path)

    try:
        harness.click(dialog, "wxID_CANCEL")
        shown_after = dialog.IsShown()
    finally:
        harness.close_window(dialog)

    assert shown_after is False


# ------------------------------------------------------------ infobar


def test_csv_preview_dlg_infobar_disables_show_hide_effects(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """csv_infobar disables both slide effects too (E3.2 follow-on).

    Measured (wxPython 4.3.1/wxWidgets 3.3.3, macOS): ShowMessage()/
    Dismiss() on a wx.InfoBar with its default slide effect never
    returns, shown or not -- see _build_infobar's own docstring.
    ``test_console_demo.py``/``test_rider_editor.py`` carry the
    sibling pins for main_frame's three InfoBars and roster_infobar.
    """
    roster = Roster()
    dialog, _view = _show(xrc_resource, roster)

    try:
        bar = harness.find_control(dialog, CSV_INFOBAR)
        effects = (bar.GetShowEffect(), bar.GetHideEffect())
    finally:
        harness.close_window(dialog)

    assert effects == (wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)


# --------------------------------------------------------------- _find


def test_csv_preview_dlg_find_given_an_unknown_control_name_raises_naming_it(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """T-5: the one ``raise`` in ``ui.views._support.find_control``."""
    dialog, view = _show(xrc_resource, Roster())

    try:
        with pytest.raises(LookupError, match=re.escape("no control named 'no_such_control'")):
            view._find("no_such_control")
    finally:
        harness.close_window(dialog)


# --------------------------------------------- the rider-editor half


_RIDER_EDITOR_ONLY_CALLS: tuple[tuple[str, tuple[Any, ...], dict[str, Any]], ...] = (
    ("show_riders", ([],), {}),
    ("show_team_choices", ([],), {}),
    ("set_delete_enabled", (), {"enabled": True}),
    ("show_form", (), {"plate": "1", "name": "A", "team": "— solo —"}),
    ("set_team_ui_visible", (), {"visible": True}),
    ("prompt_new_team_name", (), {}),
)


@pytest.mark.parametrize("case", _RIDER_EDITOR_ONLY_CALLS, ids=lambda c: c[0])
def test_csv_preview_dlg_rider_editor_only_members_raise_not_implemented(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    case: tuple[str, tuple[Any, ...], dict[str, Any]],
) -> None:
    """T-5: csv_preview_dlg has none of RiderEditor's own controls.

    Each of RidersView's six rider-editor-only members is genuinely
    unreachable here (module docstring's mirror-image split) -- never
    called by this dialog's own wxID_OK handler, which only ever
    calls the CSV trio.
    """
    method_name, args, kwargs = case
    roster = Roster()
    dialog, view = _show(xrc_resource, roster)
    method = getattr(view, method_name)

    try:
        with pytest.raises(NotImplementedError, match=re.escape("E3.4")):
            method(*args, **kwargs)
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

    ``_show`` loads the dialog, constructs ``CsvPreviewDialog`` (whose
    ``_find`` -> ``ui.views._support.find_control`` can exhaust its 25
    retries and raise a ``LookupError`` under hosted-runner load),
    shows and pumps -- all before the test's own ``try/finally``. The
    just-loaded dialog then leaks fully alive, is rerun-masked by
    ``--reruns 2``, and later trips the reap pin. ``find_control`` is
    forced to raise here so the leak is reproduced deterministically:
    red until ``_show`` closes the dialog on the way out.
    """
    roster = Roster()

    def _find_that_raises(*_args: Any, **_kwargs: Any) -> Any:  # noqa: ANN401
        raise LookupError("simulated find_control failure")

    monkeypatch.setattr(rider_editor, "find_control", _find_that_raises)

    with pytest.raises(LookupError, match=re.escape("simulated find_control failure")):
        _show(xrc_resource, roster)

    assert wx.Window.FindWindowByName(ids.CSV_PREVIEW_DLG) is None
