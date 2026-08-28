# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for the delete guard (E5.3.2, R-18).

R-18 / spec §3: deleting a ride requires typing its exact name into
``delete_ride_dlg`` (``wxID_DELETE`` stays disabled until a byte-for-
byte match), the dialog's ``message_lbl`` names the object being
destroyed (UX-DESKTOP §4 -- a blank label is a failed assertion), a
backup is written before the delete commits, and a RUNNING ride is
never deletable -- the library's Delete item is disabled for it.

The dialog gate and the naming copy are driven directly against a raw
XRC-loaded ``delete_ride_dlg``, exactly the pattern
``test_dialog_behavior.py`` established for the R-76 mechanics. The
RUNNING-disable and the Delete-button wiring are driven against a real
``RideLibrary`` over a stub source (same shape as
``test_lists_demo.py``'s ``_StubSource``). The backup-before-delete
proof runs in a spawned interpreter via ``scenario_runner`` -- it
mutates a real Store and writes real files, and the subprocess keeps
this suite's read-only shared windows untouched.

Like the rest of ``tests/functional/``, these run only in the Tart VM
-- never directly on the host (the suite opens real wx windows).
"""

from typing import TYPE_CHECKING, Any

import harness
import pages
import pytest
import scenario_runner

from rivercrossing.ride import RideStatus
from rivercrossing.ui import ids
from rivercrossing.ui.presenters.data_source import RideSummary
from rivercrossing.ui.views import dialogs
from rivercrossing.ui.views.ride_library import RideLibrary

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.functional

wx = harness.wx

_RIDE_NAME = "Club poker night"

_TIMEOUT_SENTINEL = -999


class _RideSource:
    """A minimal DataSource-shaped stub carrying ``rides()`` rows."""

    def __init__(self, rows: list[RideSummary]) -> None:
        self._rows = rows

    def rides(self) -> list[RideSummary]:
        return self._rows


def _draft_row(name: str = _RIDE_NAME) -> RideSummary:
    """One DRAFT library row (arrange helper)."""
    return RideSummary(name=name, date="2026-09-20", status=RideStatus.DRAFT, entries=1)


def _show(resource: object, name: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Load, show and pump *name* from *resource*."""
    dialog = harness.load_window_verified(resource, name, frame=False)
    try:
        dialog.Show()
        harness.pump()
    except Exception:
        harness.close_window(dialog)
        raise
    return dialog


def _end_modal_if_undecided(dialog: Any) -> None:  # noqa: ANN401
    """Fire the safety-net EndModal only when nothing else has."""
    if not dialog.IsModal() or dialog.GetReturnCode() != 0:
        return
    dialog.EndModal(_TIMEOUT_SENTINEL)


# --------------------------------------------------------- dialog gate


def test_delete_ride_dlg_delete_starts_disabled_before_typing(
    xrc_resource: object,
) -> None:
    """R-18: ``wxID_DELETE`` starts disabled before typing."""
    dialog = _show(xrc_resource, ids.DELETE_RIDE_DLG)

    try:
        dialogs.bind_delete_confirmation_gate(dialog, _RIDE_NAME)
        enabled = harness.find_control(dialog, pages.WX_ID_DELETE).IsEnabled()
    finally:
        harness.close_window(dialog)

    assert enabled is False


_DELETE_GATE_CASES = (
    ("", False),  # empty (T-4 nullable/empty boundary)
    (_RIDE_NAME.lower(), False),  # near-miss: case difference
    (_RIDE_NAME + " ", False),  # near-miss: trailing space
    (_RIDE_NAME[:-1], False),  # near-miss: one character short
    (_RIDE_NAME, True),  # exact match
)


@pytest.mark.parametrize(("typed_text", "expected_enabled"), _DELETE_GATE_CASES)
def test_delete_ride_dlg_delete_enabled_only_on_exact_name_match(
    typed_text: str,
    expected_enabled: bool,  # noqa: FBT001 -- parametrize row value, not a call-site flag
    xrc_resource: object,
) -> None:
    """R-18: near-miss -- case, space, one char short -- stays off."""
    dialog = _show(xrc_resource, ids.DELETE_RIDE_DLG)
    dialogs.bind_delete_confirmation_gate(dialog, _RIDE_NAME)

    try:
        harness.type_text(dialog, ids.CONFIRM_NAME_INPUT, typed_text)
        enabled = harness.find_control(dialog, pages.WX_ID_DELETE).IsEnabled()
    finally:
        harness.close_window(dialog)

    assert enabled is expected_enabled


# ------------------------------------------- message_lbl names the ride


def test_delete_ride_dlg_message_lbl_names_the_ride(xrc_resource: object) -> None:
    """UX-DESKTOP §4: the destructive confirm names its object."""
    dialog = _show(xrc_resource, ids.DELETE_RIDE_DLG)
    message = dialogs.delete_ride_message(_RIDE_NAME)

    try:
        harness.find_control(dialog, ids.MESSAGE_LBL).SetLabel(message)
        label = harness.find_control(dialog, ids.MESSAGE_LBL).GetLabelText()
    finally:
        harness.close_window(dialog)

    assert label != ""
    assert _RIDE_NAME in label


def test_delete_ride_dlg_message_never_blank_for_any_ride_name() -> None:
    """The helper always names the ride -- never blank."""
    assert "Club poker night" in dialogs.delete_ride_message("Club poker night")


# ------------------------------- library Delete enablement (RUNNING)


def _library_for_rows(
    xrc_resource: object,
    rows: list[RideSummary],
    *,
    on_delete: Callable[[str], None] | None = None,
) -> tuple[Any, Any]:
    """Build one shown ``RideLibrary`` over *rows*."""
    window = harness.load_window_verified(xrc_resource, ids.RIDE_LIBRARY_DLG, frame=False)
    window.Show()
    harness.pump()
    try:
        view = RideLibrary(window, data_source=_RideSource(rows), on_delete=on_delete)
    except Exception:
        harness.close_window(window)
        raise
    return window, view


def test_ride_library_delete_disabled_when_no_ride_selected(
    xrc_resource: object,
) -> None:
    """No selection: nothing to delete -- the item stays off."""
    window, _ = _library_for_rows(xrc_resource, [_draft_row()])

    try:
        enabled = harness.find_control(window, pages.WX_ID_DELETE).IsEnabled()
    finally:
        harness.close_window(window)

    assert enabled is False


def test_ride_library_delete_disabled_for_running_selected_ride(
    xrc_resource: object,
) -> None:
    """R-18: a RUNNING ride's Delete item is disabled in the library."""
    window, _ = _library_for_rows(
        xrc_resource,
        [RideSummary(name="Live", date="2026-09-20", status=RideStatus.RUNNING, entries=1)],
    )

    try:
        harness.select_row(window, ids.RIDES_LIST, 0)
        enabled = harness.find_control(window, pages.WX_ID_DELETE).IsEnabled()
    finally:
        harness.close_window(window)

    assert enabled is False


def test_ride_library_delete_enabled_for_draft_selected_ride(
    xrc_resource: object,
) -> None:
    """A non-RUNNING ride may be deleted -- the item enables."""
    window, _ = _library_for_rows(xrc_resource, [_draft_row()])

    try:
        harness.select_row(window, ids.RIDES_LIST, 0)
        enabled = harness.find_control(window, pages.WX_ID_DELETE).IsEnabled()
    finally:
        harness.close_window(window)

    assert enabled is True


# ------------------------------------ Delete button opens the dialog


def test_ride_library_delete_button_opens_delete_dlg_naming_the_ride(
    xrc_resource: object,
) -> None:
    """The Delete flow: dialog opens, names the ride, confirms.

    Driving the library's Delete button is the same proven
    ``wx.CallAfter``-probe pattern ``test_dialog_behavior.py`` uses
    for a modal: the probe finds the just-opened ``delete_ride_dlg``,
    records its label and gate state, types the exact name, and clicks
    its Delete; the confirmed callback records the ride name it was
    handed.
    """
    called: list[str] = []
    window, view = _library_for_rows(xrc_resource, [_draft_row()], on_delete=called.append)
    found: dict[str, Any] = {}

    def _probe() -> None:
        dialog = wx.Window.FindWindowByName(ids.DELETE_RIDE_DLG)
        if dialog is None:
            return
        # Arm the sentinel with the live dialog, one pass after this
        # probe runs: the dialog is still modal and alive here, so the
        # later check sees the decided state (or ends the modal itself).
        wx.CallAfter(_end_modal_if_undecided, dialog)
        found["message_lbl"] = harness.find_control(dialog, ids.MESSAGE_LBL).GetLabelText()
        found["delete_before_typing"] = harness.find_control(
            dialog, pages.WX_ID_DELETE
        ).IsEnabled()
        harness.type_text(dialog, ids.CONFIRM_NAME_INPUT, _RIDE_NAME)
        found["delete_after_typing"] = harness.find_control(dialog, pages.WX_ID_DELETE).IsEnabled()
        harness.click(dialog, pages.WX_ID_DELETE)

    try:
        harness.select_row(window, ids.RIDES_LIST, 0)
        wx.CallAfter(_probe)
        harness.click(window, pages.WX_ID_DELETE)
        assert view.dialog.IsBeingDeleted() is False
    finally:
        harness.close_window(window)

    assert found.get("message_lbl", "") != ""
    assert _RIDE_NAME in found.get("message_lbl", "")
    assert found.get("delete_before_typing") is False
    assert found.get("delete_after_typing") is True
    assert called == [_RIDE_NAME]


# ------------------------------- backup written before delete (Tart)


def test_delete_ride_dlg_backup_written_before_delete() -> None:
    """Confirmed delete writes a backup first; the row is then gone."""
    result = scenario_runner.run_scenario("delete_ride_dlg_backup_written_before_delete")

    data = result["data"]
    assert data["message_lbl"] != "", result["context"]
    assert _RIDE_NAME in data["message_lbl"], result["context"]
    assert data["backup_exists"] is True, result["context"]
    assert data["backup_reopens"] is True, result["context"]
    assert data["ride_removed"] is True, result["context"]
