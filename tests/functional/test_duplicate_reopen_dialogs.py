# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for the E5.4.1 mock-first dialogs (R-76, §15b).

File ▸ Duplicate Ride… and Ride ▸ Reopen Ride were the two §15 rows
with no frozen window until E5.4.1: ``commands.py`` routed them to
the E1.4.1 ``_UNAUTHORED_DIALOG`` sentinel, and the menu-coverage
tests asserted that. This session authored both mock-first -- their
control names are registered in spec.md §15b BEFORE any UI wiring
(task-brief E5.4.1's plan §2) -- as non-destructive confirms:

* ``duplicate_ride_dlg`` -- ``message_lbl`` names the ride, ``wxID_OK``
  "Duplicate" is the default + focused control, ``wxID_CANCEL`` cancels.
* ``reopen_ride_dlg`` -- ``message_lbl`` names the ride, ``wxID_OK``
  "Reopen" is the default + focused control, ``wxID_CANCEL`` cancels.

Both are NON-destructive, so per spec.md §13 the primary button is
the default and a reflex Enter is safe -- the exact opposite of the
destructive confirms (delete/stop/finish), whose Cancel is default +
focused. R-76's generic per-dialog machinery (Esc cancels, Enter =
the marked default, a click on the default ends the modal) is
asserted for every dialog by ``test_dialog_behavior.py``; this file
pins the facts specific to these two new surfaces: the frozen names
resolve, the message copy names the ride and is never blank, the
default really is OK, and Escape genuinely cancels without acting.

Like the rest of ``tests/functional/``, these run only in the Tart VM
-- never directly on the host (the suite opens real wx windows).
"""

from typing import TYPE_CHECKING, Any

import harness
import pages
import pytest
import wx

from rivercrossing.ui import ids
from rivercrossing.ui.views import dialogs

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.functional

_RIDE_NAME = "GORBA EPIC 2026"

_TIMEOUT_SENTINEL = -999


def _show(xrc_resource: object, name: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Load, show and pump *name* from *resource*."""
    dialog = harness.load_window_verified(xrc_resource, name, frame=False)
    try:
        dialog.Show()
        harness.pump()
    except Exception:
        harness.close_window(dialog)
        raise
    return dialog


def _send_escape(dialog: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Post a real Escape ``CHAR_HOOK`` at *dialog* (proven to work)."""
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(wx.WXK_ESCAPE)
    dialog.GetEventHandler().ProcessEvent(event)


def _end_modal_if_undecided(dialog: Any) -> None:  # noqa: ANN401
    """Fire the safety-net ``EndModal`` only when nothing else has.

    The return-code guard mirrors test_dialog_behavior.py: a decided
    dialog carries its real result here even while ``IsModal()`` is
    still true (measured on windows-latest CI).
    """
    if not dialog.IsModal() or dialog.GetReturnCode() != 0:
        return
    dialog.EndModal(_TIMEOUT_SENTINEL)


def _run_with_action(dialog: Any, action: Callable[[], None]) -> int:  # noqa: ANN401
    """Run *action* while scheduling it once the modal loop pumps.

    A safety-net ``EndModal`` is armed right after *action* so a probe
    that turns out not to end the dialog cannot hang the suite -- the
    same ``_run_with_action`` shape test_dialog_behavior.py uses.
    """
    wx.CallAfter(action)
    wx.CallAfter(_end_modal_if_undecided, dialog)
    return int(dialog.ShowModal())


# ---------------------------------------- §15b names resolve per dialog


@pytest.mark.parametrize(
    ("dialog_name", "expected_controls"),
    [
        (ids.DUPLICATE_RIDE_DLG, (ids.MESSAGE_LBL, pages.WX_ID_OK, pages.WX_ID_CANCEL)),
        (ids.REOPEN_RIDE_DLG, (ids.MESSAGE_LBL, pages.WX_ID_OK, pages.WX_ID_CANCEL)),
    ],
    ids=lambda name: name,
)
def test_e541_dialog_resolves_its_frozen_controls(
    dialog_name: str,
    expected_controls: tuple[str, ...],
    xrc_resource: object,
) -> None:
    """Every §15b-registered name resolves inside the new dialog."""
    dialog = _show(xrc_resource, dialog_name)

    try:
        resolved = {
            name: harness.find_control(dialog, name).GetName() for name in expected_controls
        }
    finally:
        harness.close_window(dialog)

    assert resolved == {name: name for name in expected_controls}


# ------------------- both are non-destructive: OK is the default


@pytest.mark.parametrize("dialog_name", [ids.DUPLICATE_RIDE_DLG, ids.REOPEN_RIDE_DLG])
def test_e541_dialog_default_is_ok_not_cancel(dialog_name: str, xrc_resource: object) -> None:
    """spec.md §13: a non-destructive confirm defaults to its primary.

    The brief's "Reopen is non-destructive so Enter-ok is fine;
    Duplicate likewise" is a frozen XRC fact here: ``wxID_OK`` is both
    the marked default and the initially focused control (its XRC
    co-declares ``<default>`` + ``<focused>``), the opposite of the
    destructive confirms' Cancel-default.
    """
    dialog = _show(xrc_resource, dialog_name)

    try:
        default_item = dialog.GetDefaultItem()
        default_name = default_item.GetName() if default_item is not None else None
    finally:
        harness.close_window(dialog)

    assert default_name == pages.WX_ID_OK


# ----------------------- Escape cancels (R-76, negative)


@pytest.mark.parametrize("dialog_name", [ids.DUPLICATE_RIDE_DLG, ids.REOPEN_RIDE_DLG])
def test_e541_dialog_escape_cancels_without_acting(dialog_name: str, xrc_resource: object) -> None:
    """R-76: Esc cancels -- never the (non-destructive) primary path."""
    dialog = _show(xrc_resource, dialog_name)

    try:
        result = _run_with_action(dialog, lambda: _send_escape(dialog))
    finally:
        harness.close_window(dialog)

    assert result == wx.ID_CANCEL


# ----------------------------- the naming copy is never blank (§4, UX)


@pytest.mark.parametrize(
    ("helper", "dialog_name"),
    [
        (dialogs.duplicate_ride_message, ids.DUPLICATE_RIDE_DLG),
        (dialogs.reopen_ride_message, ids.REOPEN_RIDE_DLG),
    ],
    ids=lambda value: getattr(value, "__name__", value),
)
def test_e541_message_helper_never_blank_and_names_the_ride(
    helper: Callable[[str], str],
    dialog_name: str,
    xrc_resource: object,
) -> None:
    """UX-DESKTOP §4: the confirm names its ride; a blank line fails.

    The message helper's output is what the app writes into
    ``message_lbl`` (``_open_ride_confirm``), so a blank return here
    would render a blank confirmation -- a failed assertion, never a
    cosmetic one (the same rule E5.2.2 pinned for resume_dlg).
    """
    dialog = _show(xrc_resource, dialog_name)

    try:
        harness.find_control(dialog, ids.MESSAGE_LBL).SetLabel(helper(_RIDE_NAME))
        label = harness.find_control(dialog, ids.MESSAGE_LBL).GetLabelText()
    finally:
        harness.close_window(dialog)

    assert label != ""
    assert _RIDE_NAME in label
