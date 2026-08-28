# SPDX-License-Identifier: GPL-3.0-only
"""The resume-dialog decision core (E5.2.2, R-52).

R-52: on launch with a running ride, ``resume_dlg`` always appears;
session bookkeeping distinguishes a clean quit from a crash and words
the dialog accordingly (spec §3): ``closed_at`` present -> "You quit
at 12:41 -- the ride kept running"; ``closed_at`` NULL -> crash,
"closed unexpectedly at 12:41" (last heartbeat). Continuing preserves
start time and all data -- every crossing was committed when it
happened.

Zero ``wx``: :func:`resume_message` and :func:`resume_dialog_for` are
plain functions over plain data, unit-tested headless the way every
other piece of UI *logic* in this codebase is (R-71, module-
skeletons.md S1). ``app.py`` is the one wx-touching caller -- it loads
``resume_dlg``, writes the copy :func:`resume_message` returns into
its ``message_lbl``, binds ``continue_btn``/``library_btn`` to end the
modal, and maps the result to either the store-loaded engine or the
library (E5.2.2's launch flow).
"""

from typing import TYPE_CHECKING

from rivercrossing.store import PreviousSession, SessionState
from rivercrossing.ui import ids

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ["resume_dialog_for", "resume_message"]

# spec §3's own time shape ("You quit at 12:41"): local 24-hour,
# zero-padded hour and minute. The store hands naive local datetimes
# (_from_epoch's inverse contract), so no tz conversion is needed here.
_RESUME_TIME_FORMAT = "%H:%M"


def resume_message(ride_name: str, state: SessionState, at: datetime) -> str:
    """Return ``resume_dlg``'s ``message_lbl`` copy for a ride.

    Words R-52's two readings from spec §3, naming the ride so the
    operator knows exactly which ride the dialog is about, and showing
    the session's end as local 24-hour time. The XRC label carries the
    frozen ``message_lbl`` name and an empty default; app.py writes
    this copy before showing the dialog, and a blank label is a failed
    assertion, not a cosmetic one (task-brief E5.2.2).

    Args:
        ride_name: The running ride's display name.
        state: How the previous session ended.
        at: The instant the copy's time shows -- ``closed_at`` for a
            quit, the last heartbeat for a crash (naive local, per
            the store's own contract).

    Returns:
        The message text naming *ride_name* and the session's end.

    Raises:
        ValueError: *state* is ``SessionState.CLEAN_QUIT`` -- a clean
            quit has no running ride to resume, so wording one is a
            caller bug, never a blank or invented line.
    """
    if state is not SessionState.RUNNING_AT_EXIT and state is not SessionState.CRASHED:
        raise ValueError(f"no resume copy for {state}")
    time_text = at.strftime(_RESUME_TIME_FORMAT)
    if state is SessionState.RUNNING_AT_EXIT:
        return (
            f'"{ride_name}" is still running. You quit at {time_text} — '
            "the ride kept running on the wall clock."
        )
    return (
        f'"{ride_name}" is still running. The app closed unexpectedly at '
        f"{time_text} — the ride kept running on the wall clock."
    )


def resume_dialog_for(session: PreviousSession) -> str | None:
    """Return the frozen XRC dialog to open at launch, or ``None``.

    R-52's launch decision: a ride was running at the previous exit
    (``active_ride_id`` set) -- a clean quit-keep-running, or a crash
    with a running ride -- so ``resume_dlg`` must appear. A clean quit
    with no ride, or a crash with no ride, has nothing to resume and
    opens no dialog.

    Args:
        session: The previous session's resume record.

    Returns:
        ``ids.RESUME_DLG`` when a ride was running at the previous
        exit; ``None`` otherwise.
    """
    if session.state is SessionState.RUNNING_AT_EXIT:
        return ids.RESUME_DLG
    if session.state is SessionState.CRASHED and session.ride_id is not None:
        return ids.RESUME_DLG
    return None
