# SPDX-License-Identifier: GPL-3.0-only
"""The quit-confirm decision core: which dialog, what it means (R-51).

Phase 8, P8-D1: the app never exits without confirmation. Quitting
while ``RideStatus.RUNNING`` shows ``exit_running_dlg`` -- the
already-authored Cancel / Finish-ride-first / Quit-keep-running row
(spec.md §3, §15) -- because quitting does not stop a running ride, it
just keeps timing on the wall clock. Every other status has no ride to
protect, so it gets the plain destructive confirm, ``exit_confirm_dlg``
(A2). Both dialogs' own XRC marks Cancel as the default *and* the
initially-focused control (R-76), so a reflex Enter is always safe.

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

__all__ = ["QuitOutcome", "dialog_for_status", "outcome_for"]


class QuitOutcome(Enum):
    """What a quit-confirm dialog's own result means for the caller.

    ``FINISH_FIRST`` is reachable only from ``exit_running_dlg``:
    ``exit_confirm_dlg`` carries no ``finish_first_btn`` at all
    (:func:`dialog_for_status`'s non-``RUNNING`` branch), so a caller
    never has a real id for it outside that one dialog.
    """

    QUIT = "quit"
    STAY = "stay"
    FINISH_FIRST = "finish_first"


def dialog_for_status(status: RideStatus) -> str:
    """Return the frozen XRC dialog name to confirm quitting *status*.

    Args:
        status: The active ride's current lifecycle status.

    Returns:
        ``ids.EXIT_RUNNING_DLG`` for ``RideStatus.RUNNING``;
        ``ids.EXIT_CONFIRM_DLG`` for every other status.
    """
    if status == RideStatus.RUNNING:
        return ids.EXIT_RUNNING_DLG
    return ids.EXIT_CONFIRM_DLG


def outcome_for(result: int, *, ok_id: int, finish_first_id: int | None = None) -> QuitOutcome:
    """Map a quit-confirm dialog's ``ShowModal()`` result to an outcome.

    Args:
        result: The dialog's ``ShowModal()`` return value.
        ok_id: The runtime id ``wxID_OK`` -- the affirmative "Quit"
            button both dialogs carry -- resolved to.
        finish_first_id: ``finish_first_btn``'s own runtime id, when
            the dialog shown was ``exit_running_dlg``; ``None`` for
            ``exit_confirm_dlg``, which carries no such button.

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
