# SPDX-License-Identifier: GPL-3.0-only
"""Functional tests for R-78 "Check for Rider Issues…".

Drives the fake GORBA-style fixture through a real CSV preview, then
drives ``rider_issues_dlg`` over a real in-memory
:class:`~rivercrossing.roster.Roster`, following ``test_csv_preview.py``
and ``test_team_editor.py``'s own precedent: never a mock in place of
the real roster or the real dialog.
"""

from pathlib import Path
from typing import Any

import harness
import pytest

from rivercrossing import csvio
from rivercrossing.roster import EntryMode, EntryType, Roster
from rivercrossing.ui import ids
from rivercrossing.ui.views.rider_editor import CsvPreviewDialog
from rivercrossing.ui.views.rider_issues import RiderIssuesView

pytestmark = pytest.mark.functional

_FIXTURE = Path(__file__).resolve().parents[1] / "unit" / "fixtures" / "csv" / "gorba_fake.csv"


def _show_csv_preview(xrc_resource: Any, roster: Roster) -> tuple[Any, CsvPreviewDialog]:  # noqa: ANN401
    """Load csv_preview_dlg, wire it live over *roster*, show, pump."""
    dialog = harness.load_window_verified(xrc_resource, ids.CSV_PREVIEW_DLG, frame=False)
    try:
        view = CsvPreviewDialog(dialog, roster=roster)
        dialog.Show()
        harness.pump()
    except Exception:  # Fault A: any post-load failure must close the dialog
        harness.close_window(dialog)
        raise
    return dialog, view


def _show_issues(xrc_resource: Any, roster: Roster) -> tuple[Any, RiderIssuesView]:  # noqa: ANN401
    """Load rider_issues_dlg, wire it live over *roster*, show, pump."""
    dialog = harness.load_window_verified(xrc_resource, ids.RIDER_ISSUES_DLG, frame=False)
    try:
        view = RiderIssuesView(dialog, roster=roster)
        dialog.Show()
        harness.pump()
    except Exception:  # Fault A: any post-load failure must close the dialog
        harness.close_window(dialog)
        raise
    return dialog, view


def _issue_rows(dialog: Any) -> tuple[tuple[str, str, str], ...]:  # noqa: ANN401
    """Return every issues_list row as (plate, name, issue) text."""
    model = harness.find_control(dialog, ids.ISSUES_LIST).GetModel()
    return tuple(
        tuple(model.GetValueByRow(row, col) for col in range(3)) for row in range(model.GetCount())
    )


def test_fake_fixture_imports_with_warnings_and_keeps_the_team_of_one(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """The fake sheet imports and keeps its size-1 team."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    dialog, view = _show_csv_preview(xrc_resource, roster)

    try:
        view.presenter.on_pick_csv_import(_FIXTURE)
        import_enabled = view.ok_btn.IsEnabled()
        committed = view.presenter.on_confirm_csv_import()
    finally:
        harness.close_window(dialog)

    assert import_enabled is True
    assert committed is True
    lone = next(entry for entry in roster.entries if entry.display_name == "fake lone wolf")
    assert lone.team_size == 1
    assert lone.type is EntryType.TEAM


def test_rider_issues_dialog_lists_issues_and_converts_the_team_of_one(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """The dialog lists the import's issues and converts one."""
    roster = Roster(entry_mode=EntryMode.MIXED)
    csvio.commit(csvio.preview(_FIXTURE, roster))

    dialog, view = _show_issues(xrc_resource, roster)
    try:
        rows = _issue_rows(dialog)
        lone_index = next(i for i, row in enumerate(rows) if row[1] == "fake lone wolf")
        harness.select_row(dialog, ids.ISSUES_LIST, lone_index)
        convert_enabled = view.convert_solo_btn.IsEnabled()
        harness.click(dialog, ids.CONVERT_SOLO_BTN)
        names = {entry.display_name for entry in roster.entries}
    finally:
        harness.close_window(dialog)

    assert any(row[1] == "fake lone wolf" for row in rows)
    assert any("duplicate rider name" in row[2] for row in rows)
    assert convert_enabled is True
    assert "fake lone wolf" not in names
    assert "Nikola Tesla" in names
