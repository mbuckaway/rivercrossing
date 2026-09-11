# SPDX-License-Identifier: GPL-3.0-only
"""The quit-confirm decision core: which dialog, what it means (R-51).

Phase 8, P8-D1: the app never exits without confirmation. Quitting
while ``RideStatus.RUNNING`` shows ``exit_running_dlg`` -- the
already-authored Cancel / Finish-ride-first / Quit-keep-running row
(spec.md §3, §15) -- because quitting does not stop a running ride, it
just keeps timing on the wall clock. Every other status has no ride to
protect, so it gets the plain destructive confirm. Phase 11 H2
retired that XRC dialog (``exit_confirm_dlg``) for the native
``std_dialogs.show_confirm``, so :func:`dialog_for_status` returns
``None`` for those statuses and the frozen copy lives in the
:data:`EXIT_CONFIRM_*` constants below instead of a deleted .xrc
window. The running dialog's own XRC marks Cancel as the default
*and* the initially-focused control (R-76), and ``show_confirm``
defaults to Cancel too, so a reflex Enter is always safe.

Zero ``wx``: :func:`dialog_for_status` and :func:`outcome_for` are
plain functions over plain data, unit-tested headless the way every
other piece of UI *logic* in this codebase is (R-71, module-
skeletons.md S1). ``app.py`` is the one wx-touching caller -- it
resolves the raw ``ShowModal()`` result ids (``wx.ID_OK``, and
``finish_first_btn``'s own XRC-generated id, present only on
``exit_running_dlg``) and threads them through :func:`outcome_for`.
"""

from enum import Enum

from rivercrossing.ride import RideStatus
from rivercrossing.ui import ids

__all__ = [
    "EXIT_CONFIRM_CANCEL_LABEL",
    "EXIT_CONFIRM_MESSAGE",
    "EXIT_CONFIRM_OK_LABEL",
    "EXIT_CONFIRM_TITLE",
    "QuitOutcome",
    "dialog_for_status",
    "outcome_for",
    "running_exit_message",
]

# The native no-ride confirm's frozen copy (H2), moved here from the
# retired ``exit_confirm_dlg`` XRC. ``running_exit_message`` is this
# module's other piece of quit-flow copy, so both quit questions read
# in one place and stay unit-tested headlessly.
EXIT_CONFIRM_TITLE = "Quit RiverCrossing?"
EXIT_CONFIRM_MESSAGE = "Are you sure you want to quit? No ride is running."
EXIT_CONFIRM_OK_LABEL = "Quit"
EXIT_CONFIRM_CANCEL_LABEL = "Cancel"


class QuitOutcome(Enum):
    """What a quit-confirm dialog's own result means for the caller.

    ``FINISH_FIRST`` is reachable only from ``exit_running_dlg``:
    the native no-ride confirm carries no ``finish_first_btn`` at all
    (:func:`dialog_for_status`'s non-``RUNNING`` branch), so a caller
    never has a real id for it outside that one dialog.
    """

    QUIT = "quit"
    STAY = "stay"
    FINISH_FIRST = "finish_first"


def dialog_for_status(status: RideStatus) -> str | None:
    """Return the frozen XRC dialog name to confirm quitting *status*.

    Args:
        status: The active ride's current lifecycle status.

    Returns:
        ``ids.EXIT_RUNNING_DLG`` for ``RideStatus.RUNNING``; ``None``
        for every other status, which H2 answers with the native
        :func:`~rivercrossing.ui.std_dialogs.show_confirm` dialog
        instead of a loaded XRC window.
    """
    if status == RideStatus.RUNNING:
        return ids.EXIT_RUNNING_DLG
    return None


def outcome_for(result: int, *, ok_id: int, finish_first_id: int | None = None) -> QuitOutcome:
    """Map a quit-confirm dialog's ``ShowModal()`` result to an outcome.

    Args:
        result: The dialog's ``ShowModal()`` return value.
        ok_id: The runtime id ``wxID_OK`` -- the affirmative "Quit"
            button both confirm shapes carry -- resolved to.
        finish_first_id: ``finish_first_btn``'s own runtime id, when
            the dialog shown was ``exit_running_dlg``; ``None`` for
            the native no-ride confirm, which carries no such button.

    Returns:
        ``QUIT`` if *result* equals *ok_id*; ``FINISH_FIRST`` if
        *result* equals *finish_first_id* (never true when that is
        ``None``); ``STAY`` otherwise -- covers ``wxID_CANCEL`` and an
        Escape, which wx's own built-in handling also ends with
        ``wxID_CANCEL`` (R-76).
    """
    if result == ok_id:
        return QuitOutcome.QUIT
    if finish_first_id is not None and result == finish_first_id:
        return QuitOutcome.FINISH_FIRST
    return QuitOutcome.STAY


def running_exit_message(ride_name: str) -> str:
    """Return ``exit_running_dlg``'s ``message_lbl`` copy for a ride.

    Interpolates the running ride's name into the wall-clock
    reassurance spec §3/R-51 promises ("the wall clock keeps counting
    while the app is closed"), so the operator knows exactly which
    ride quitting keeps running. The XRC label carries the frozen
    ``message_lbl`` name and a fallback copy; app.py writes this
    ride-naming copy before showing the dialog, and a blank label is
    a failed assertion, not a cosmetic one.

    Args:
        ride_name: The running ride's display name.

    Returns:
        The message text naming *ride_name*.
    """
    return (
        f"{ride_name} is still running. Quitting won't stop the ride — "
        "it keeps timing on the wall clock, and you'll be asked to "
        "continue when you reopen."
    )
