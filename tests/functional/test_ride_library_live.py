# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for the live library + the two new routes (E5.4.1).

task-brief E5.4.1's "library live on the real DB": ``ride_library_dlg``
lists real rides from the Store, **Open** loads the selected ride and
switches the console context (``Store.load_engine`` + the E5.2.2
resume wiring), **Duplicate** copies setup + roster to a new DRAFT ride
with no timing data (R-15) and refreshes the list, **New** opens the
ride-setup flow, **Delete** keeps the E5.3 R-18 path, and the two menu
routes that used to hit the E1.4.1 sentinel (File ▸ Duplicate Ride…,
Ride ▸ Reopen Ride) now ask the native confirm prompts and act.

Every flow mutates process-global state (the library is a modal, and
Open rebuilds the console around the store engine), so each runs in a
fresh, spawned interpreter via ``console_subprocess_scenarios.py`` --
the same isolation the resume/quit scenarios use. The native prompts'
copy contract lives in ``test_duplicate_reopen_dialogs.py``; this
file proves the flows.

Like the rest of ``tests/functional/``, these run only in the Tart VM
-- never directly on the host (the suite opens real wx windows).
"""

from typing import TYPE_CHECKING, Any

import harness
import pages
import pytest
import scenario_runner

from rivercrossing.ride import RideStatus
from rivercrossing.ui import app as app_module
from rivercrossing.ui import ids, std_dialogs
from rivercrossing.ui.presenters.data_source import RideSummary
from rivercrossing.ui.views.ride_library import RideLibrary

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.functional

wx = harness.wx


class _RideSource:
    """A minimal DataSource-shaped stub carrying ``rides()`` rows."""

    def __init__(self, rows: list[RideSummary]) -> None:
        self._rows = rows

    def rides(self) -> list[RideSummary]:
        return self._rows


def _draft_row(name: str = "GORBA EPIC 2026") -> RideSummary:
    """One DRAFT library row (arrange helper)."""
    return RideSummary(name=name, date="2026-09-20", status=RideStatus.DRAFT, entries=1)


# -------------------------------------- store-backed flows (Tart)


def test_ride_library_open_switches_console_to_the_ride() -> None:
    """Open on a RUNNING store ride swaps the console onto it."""
    result = scenario_runner.run_scenario("library_live_open_switches_console_context")

    data = result["data"]
    assert data["status_label"] == "RUNNING", result["context"]
    assert data["feed_rows"] == 1, result["context"]
    assert data["feed_plate"] == "12", result["context"]


def test_ride_library_delete_removes_the_exact_selected_row_and_refreshes() -> None:
    """W10: Delete removes the selected row by id; the list refreshes.

    Two store rides share the name "GORBA EPIC 2026" (``ride.name``
    has no UNIQUE constraint, and the name-based resolution the old
    delete callback did would remove the FIRST match -- the wrong
    row). Selecting the second row and confirming Delete must remove
    that exact row, and the refreshed list must show the surviving
    first row immediately.
    """
    result = scenario_runner.run_scenario("library_live_delete_removes_exact_row_and_refreshes")

    data = result["data"]
    assert data["delete_dlg_shown"] is True, result["context"]
    assert data["library_refreshed"] is True, result["context"]
    assert data["rows_after"] == [["GORBA EPIC 2026", "DRAFT"]], result["context"]
    assert data["survivor_id"] == data["first_ride_id"], result["context"]
    assert data["backup_exists"] is True, result["context"]


def test_ride_library_duplicate_appears_as_new_draft_with_no_timing() -> None:
    """R-15: the copy is a DRAFT ride, same roster, no timing.

    Both rows read DRAFT: the library's Status column shows the stored
    ``ride.status``, which the facade does not sync when events are
    appended (the documented E5.4 engine-sync gap) -- the RUNNING
    source is DRAFT in the store too. The copy's DRAFT + empty
    crossings/cards/audit + fresh seed are the R-15 proof.
    """
    result = scenario_runner.run_scenario("library_live_duplicate_appears_as_new_draft")
    data = result["data"]
    assert data["duplicate_confirm_shown"] is True, result["context"]
    assert data["duplicate_message"] != "", result["context"]
    assert "GORBA EPIC 2026" in data["duplicate_message"], result["context"]
    assert data["rows_after"] == [
        ["GORBA EPIC 2026", "DRAFT"],
        ["GORBA EPIC 2026 (copy)", "DRAFT"],
    ], result["context"]
    assert data["copy_name"] == "GORBA EPIC 2026 (copy)", result["context"]
    assert data["copy_status"] == "draft", result["context"]
    assert data["copy_entries"] == 2, result["context"]
    assert data["copy_roster"] == [
        ["12", "Alice"],
        ["77", "Trail Blazers"],
    ], result["context"]
    assert data["copy_crossings"] == 0, result["context"]
    assert data["copy_cards"] == 0, result["context"]
    assert data["copy_audit"] == 0, result["context"]
    assert data["fresh_seed"] is True, result["context"]


def test_duplicate_ride_menu_route_opens_confirm_and_duplicates() -> None:
    """File ▸ Duplicate Ride… resolves to the real dialog and copies."""
    result = scenario_runner.run_scenario("duplicate_ride_menu_route_opens_confirm_and_duplicates")

    data = result["data"]
    assert data["duplicate_confirm_shown"] is True, result["context"]
    assert data["duplicate_message"] != "", result["context"]
    assert "GORBA EPIC 2026" in data["duplicate_message"], result["context"]
    assert data["status_text"] == "Duplicated as GORBA EPIC 2026 (copy)", result["context"]
    assert data["ride_count"] == 2, result["context"]
    assert data["copy_name"] == "GORBA EPIC 2026 (copy)", result["context"]
    assert data["copy_status"] == "draft", result["context"]


def test_reopen_ride_menu_route_opens_confirm_and_reopens() -> None:
    """Ride ▸ Reopen Ride asks the native prompt and reopens."""
    result = scenario_runner.run_scenario("reopen_ride_menu_route_opens_confirm_and_reopens")

    data = result["data"]
    assert data["reopen_confirm_shown"] is True, result["context"]
    assert data["reopen_message"] != "", result["context"]
    assert "Club poker night" in data["reopen_message"], result["context"]
    assert data["status_label"] == "REOPENED", result["context"]


# -------------------------------------- library action enablement (wx)


def _library_for_rows(  # noqa: PLR0913 -- (xrc_resource, rows) + the two injected callbacks
    xrc_resource: object,
    rows: list[RideSummary],
    *,
    on_open: Callable[[RideSummary], None] | None = None,
    on_duplicate: Callable[[RideSummary], None] | None = None,
) -> tuple[Any, Any]:
    """Build one shown ``RideLibrary`` over *rows*."""
    window = harness.load_window_verified(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    window.Show()
    harness.pump()
    try:
        view = RideLibrary(
            window,
            data_source=_RideSource(rows),
            on_open=on_open,
            on_duplicate=on_duplicate,
        )
    except Exception:
        harness.close_window(window)
        raise
    return window, view


def test_ride_library_open_disabled_when_no_ride_selected(
    xrc_resource: object,
) -> None:
    """No selection: nothing to open -- Open stays off."""
    window, _ = _library_for_rows(xrc_resource, [_draft_row()])

    try:
        enabled = harness.find_control(window, pages.WX_ID_OPEN).IsEnabled()
    finally:
        harness.close_window(window)

    assert enabled is False


def test_ride_library_duplicate_disabled_when_no_ride_selected(
    xrc_resource: object,
) -> None:
    """No selection: nothing to duplicate -- Duplicate stays off."""
    window, _ = _library_for_rows(xrc_resource, [_draft_row()])

    try:
        enabled = harness.find_control(window, ids.DUPLICATE_BTN).IsEnabled()
    finally:
        harness.close_window(window)

    assert enabled is False


def test_ride_library_open_and_duplicate_enabled_for_selected_ride(
    xrc_resource: object,
) -> None:
    """A selected ride enables Open and Duplicate (unlike Delete)."""
    window, _ = _library_for_rows(
        xrc_resource,
        [RideSummary(name="Live", date="2026-09-20", status=RideStatus.RUNNING, entries=1)],
    )

    try:
        harness.select_row(window, ids.RIDES_LIST, 0)
        open_enabled = harness.find_control(window, pages.WX_ID_OPEN).IsEnabled()
        duplicate_enabled = harness.find_control(window, ids.DUPLICATE_BTN).IsEnabled()
    finally:
        harness.close_window(window)

    assert (open_enabled, duplicate_enabled) == (True, True)


def test_ride_library_open_forwards_the_selected_ride(xrc_resource: object) -> None:
    """Open hands the selected row to the injected callback (E5.4.1)."""
    opened: list[RideSummary] = []
    window, _ = _library_for_rows(xrc_resource, [_draft_row()], on_open=opened.append)

    try:
        harness.select_row(window, ids.RIDES_LIST, 0)
        harness.click(window, pages.WX_ID_OPEN)
    finally:
        harness.close_window(window)

    assert opened == [_draft_row()]


def test_ride_library_duplicate_forwards_the_selected_ride(
    xrc_resource: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicate hands the selected row to the injected callback."""
    duplicated: list[RideSummary] = []
    window, _ = _library_for_rows(xrc_resource, [_draft_row()], on_duplicate=duplicated.append)
    found: dict[str, Any] = {}

    def _record_and_ok(  # noqa: PLR0913, PLR0917 -- mirrors std_dialogs.show_prompt's signature
        _parent: object,
        title: str,
        message: str,
        ok_label: str,
        cancel_label: str,
    ) -> int:
        found["prompt_shown"] = (title, message, ok_label, cancel_label)
        return wx.ID_OK

    try:
        monkeypatch.setattr(std_dialogs, "show_prompt", _record_and_ok)
        harness.select_row(window, ids.RIDES_LIST, 0)
        harness.click(window, ids.DUPLICATE_BTN)
    finally:
        harness.close_window(window)

    assert found.get("prompt_shown") is not None
    assert duplicated == [_draft_row()]


# -------------------------------------- route negatives (no store ride)


def test_duplicate_ride_route_without_a_store_ride_posts_notice(
    wx_app: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative: no store ride open -- the route says so, asks nothing.

    The demo bootstrap has no ``active_ride_id`` (no store-backed ride
    was opened), so File ▸ Duplicate Ride… must post a notice rather
    than raise the native prompt -- the honest no-op the E1.4.1
    sentinel used to be, kept for the no-ride case now that the
    confirm is real.
    """
    frame = app_module.build_main_window(wx_app)
    prompts: list[tuple[str, str, str, str]] = []

    def _record_prompt(  # noqa: PLR0913, PLR0917 -- mirrors std_dialogs.show_prompt's signature
        _parent: object,
        title: str,
        message: str,
        ok_label: str,
        cancel_label: str,
    ) -> int:
        prompts.append((title, message, ok_label, cancel_label))
        return wx.ID_OK

    try:
        monkeypatch.setattr(std_dialogs, "show_prompt", _record_prompt)
        frame.Show()
        frame.Layout()
        harness.pump()
        harness.fire_menu_event(frame, "mi_duplicate_ride")
        status_text = frame.GetStatusBar().GetStatusText(0)
    finally:
        harness.release_main_window(wx_app, frame)

    assert status_text == "Duplicate Ride… — no store ride is open"
    assert prompts == []


def test_reopen_ride_route_on_non_finished_ride_refuses_and_notices() -> None:
    """Negative: reopening a non-FINISHED ride refuses after confirm.

    The bootstrap console is DRAFT (a fresh launch starts no ride,
    E5.4.2); confirming the (real) reopen dialog must surface the
    engine's refusal on the status bar, never crash. Runs in a fresh
    interpreter (scenario) because driving a modal off a shared-worker
    bootstrap hit the suite's documented native-wx churn (scenario
    docstring).
    """
    result = scenario_runner.run_scenario("reopen_ride_route_on_non_finished_refuses")

    data = result["data"]
    assert data["reopen_confirm_shown"] is True, result["context"]
    assert data["reopen_message"] != "", result["context"]
    assert data["status_text"] == "Cannot reopen: cannot reopen from draft", result["context"]
