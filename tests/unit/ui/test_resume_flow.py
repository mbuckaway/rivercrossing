# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the resume-dialog decision core (E5.2.2, R-52).

``resume_flow.py`` imports no ``wx`` at all, so the wording function
(``resume_message``) and the launch decision (``resume_dialog_for``)
are exactly the kind of logic R-71's >=90% branch-coverage gate is
meant to cover -- ``tests/functional/test_resume_dlg.py`` covers what
only a real ``wx.Dialog`` can prove (that ``resume_dlg`` actually
shows at launch, that its buttons end the modal, and that the
resumed console shows the right elapsed).

Spec §3 / R-52: closed_at present -> "You quit at 12:41 -- the ride
kept running"; closed_at NULL -> crash, "closed unexpectedly at
12:41" (last heartbeat). A blank ``message_lbl`` is a failed
assertion, never a cosmetic one (task-brief E5.2.2).
"""

import re
import string
from datetime import datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.store import PreviousSession, SessionState
from rivercrossing.ui import ids, resume_flow

# The copy-time the wording function formats; pinned local 24-hour.
_FIXED_AT = datetime(2026, 9, 20, 12, 41)  # noqa: DTZ001 -- naive local, the store's own contract

# --- resume_message: quit vs crash copy (spec §3, R-52) ------------


def test_resume_message_given_running_at_exit_names_ride_and_quit_time() -> None:
    """RUNNING_AT_EXIT words the quit line naming the ride."""
    message = resume_flow.resume_message(
        "GORBA EPIC 2026", SessionState.RUNNING_AT_EXIT, _FIXED_AT
    )

    assert "GORBA EPIC 2026" in message
    assert "You quit at 12:41" in message
    assert "kept running" in message
    assert len(message) > 40


def test_resume_message_given_crashed_names_ride_and_closed_unexpectedly() -> None:
    """CRASHED: "closed unexpectedly at <time>" (last heartbeat)."""
    message = resume_flow.resume_message("GORBA EPIC 2026", SessionState.CRASHED, _FIXED_AT)

    assert "GORBA EPIC 2026" in message
    assert "closed unexpectedly at 12:41" in message
    assert len(message) > 40


def test_resume_message_quit_and_crash_copies_differ_from_each_other() -> None:
    """The two wordings are distinguishable, never the same line."""
    quit_copy = resume_flow.resume_message("Ride", SessionState.RUNNING_AT_EXIT, _FIXED_AT)
    crash_copy = resume_flow.resume_message("Ride", SessionState.CRASHED, _FIXED_AT)

    assert quit_copy != crash_copy
    assert "You quit" in quit_copy
    assert "closed unexpectedly" in crash_copy


def test_resume_message_formats_the_time_as_local_24_hour_zero_padded() -> None:
    """The copy's time is local 24-hour HH:MM, zero-padded (spec §3)."""
    message = resume_flow.resume_message(
        "Ride",
        SessionState.RUNNING_AT_EXIT,
        datetime(2026, 9, 20, 9, 5),  # noqa: DTZ001
    )

    assert "You quit at 09:05" in message


def test_resume_message_given_clean_quit_raises_value_error() -> None:
    """CLEAN_QUIT has no ride to resume; wording it is a caller bug."""
    with pytest.raises(ValueError, match=re.escape("no resume copy for SessionState.CLEAN_QUIT")):
        resume_flow.resume_message("Ride", SessionState.CLEAN_QUIT, _FIXED_AT)


# --- resume_message property: any ride name + resumable state (T-7) --


@given(
    ride_name=st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=30),
    state=st.sampled_from([SessionState.RUNNING_AT_EXIT, SessionState.CRASHED]),
)
def test_resume_message_given_any_ride_name_and_resumable_state_contains_it_verbatim(
    ride_name: str, state: SessionState
) -> None:
    """Invariant: any non-empty ride name lands verbatim in the copy."""
    message = resume_flow.resume_message(ride_name, state, _FIXED_AT)

    assert ride_name in message
    assert len(message) > 40


@given(
    at=st.datetimes(),
    state=st.sampled_from([SessionState.RUNNING_AT_EXIT, SessionState.CRASHED]),
)
def test_resume_message_given_any_time_always_carries_a_24_hour_stamp(
    at: datetime, state: SessionState
) -> None:
    """Invariant: every copy carries a local 24-hour HH:MM time."""
    message = resume_flow.resume_message("Ride", state, at)

    assert re.search(r"\d{2}:\d{2}", message) is not None


# --- resume_dialog_for: the launch decision (R-52) ------------------


def _session(state: SessionState, ride_id: int | None) -> PreviousSession:
    """Build one PreviousSession for the decision table."""
    return PreviousSession(state=state, ride_id=ride_id, ended_at=_FIXED_AT)


RESUME_DIALOG_CASES = (
    # The decision table: only a session that carried a running ride
    # (RUNNING_AT_EXIT, or CRASHED with ride_id) opens the dialog.
    (SessionState.CLEAN_QUIT, None, None),
    (SessionState.CRASHED, None, None),
    (SessionState.RUNNING_AT_EXIT, 7, ids.RESUME_DLG),
    (SessionState.CRASHED, 7, ids.RESUME_DLG),
)


@pytest.mark.parametrize(("state", "ride_id", "expected"), RESUME_DIALOG_CASES)
def test_resume_dialog_for_given_session_state_and_ride_returns_dialog_or_none(
    state: SessionState, ride_id: int | None, expected: str | None
) -> None:
    """R-52: a running ride at exit opens the resume dialog."""
    result = resume_flow.resume_dialog_for(_session(state, ride_id))

    assert result == expected
