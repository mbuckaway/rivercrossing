# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the quit-confirm decision core (Phase 8, P8-D1).

``quit_flow.py`` imports no ``wx`` at all, so ``dialog_for_status``'s
four-status mapping and ``outcome_for``'s result matrix are exactly
the kind of logic R-71's >=90% branch-coverage gate is meant to
cover -- ``tests/functional/test_quit_flow_wx.py`` covers what only
a real ``wx.Dialog`` can prove (that the right dialog actually
shows, and that clicking its buttons genuinely ends the modal with
these ids).
"""

import string

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from rivercrossing.ride import RideStatus
from rivercrossing.ui import ids, quit_flow

# --- dialog_for_status: all four statuses (T-3/T-13) ---------------

DIALOG_FOR_STATUS_CASES = (
    (RideStatus.DRAFT, ids.EXIT_CONFIRM_DLG),
    (RideStatus.RUNNING, ids.EXIT_RUNNING_DLG),
    (RideStatus.FINISHED, ids.EXIT_CONFIRM_DLG),
    (RideStatus.REOPENED, ids.EXIT_CONFIRM_DLG),
)


@pytest.mark.parametrize(("status", "expected_dialog"), DIALOG_FOR_STATUS_CASES)
def test_dialog_for_status_given_each_ride_status_returns_expected_dialog(
    status: RideStatus, expected_dialog: str
) -> None:
    """RUNNING alone gets exit_running_dlg; other statuses don't."""
    result = quit_flow.dialog_for_status(status)

    assert result == expected_dialog


# --- outcome_for: the full (result, finish_first_id) matrix --------
# ok_id is held constant at _OK_ID -- it is app.py's own resolved
# wxID_OK, not a case-varying input -- so every row parametrizes only
# the two fields that actually change shape between the two dialogs.

_OK_ID = 5100  # a stand-in wxID_OK-shaped int
_FINISH_FIRST_ID = 40001  # a stand-in XRC-generated id, distinct from _OK_ID
_CANCEL_ID = 5101  # a stand-in wxID_CANCEL-shaped int

OUTCOME_FOR_CASES = (
    # exit_confirm_dlg shape: no finish_first_id at all.
    (_OK_ID, None, quit_flow.QuitOutcome.QUIT),
    (_CANCEL_ID, None, quit_flow.QuitOutcome.STAY),
    # exit_running_dlg shape: finish_first_id is a real, distinct id.
    (_OK_ID, _FINISH_FIRST_ID, quit_flow.QuitOutcome.QUIT),
    (_FINISH_FIRST_ID, _FINISH_FIRST_ID, quit_flow.QuitOutcome.FINISH_FIRST),
    (_CANCEL_ID, _FINISH_FIRST_ID, quit_flow.QuitOutcome.STAY),
)


@pytest.mark.parametrize(("result", "finish_first_id", "expected"), OUTCOME_FOR_CASES)
def test_outcome_for_given_result_and_known_ids_matches_expected_outcome(
    result: int, finish_first_id: int | None, expected: quit_flow.QuitOutcome
) -> None:
    """T-3: every branch of the ok/finish_first/else chain."""
    outcome = quit_flow.outcome_for(result, ok_id=_OK_ID, finish_first_id=finish_first_id)

    assert outcome is expected


# --- property: ok_id always wins over finish_first_id (T-7) ------


@given(finish_first_id=st.one_of(st.none(), st.integers(min_value=1, max_value=10_000)))
def test_outcome_for_given_result_equals_ok_id_always_returns_quit(
    finish_first_id: int | None,
) -> None:
    """Property: a matching ok_id wins over finish_first_id."""
    assume(finish_first_id != _OK_ID)

    outcome = quit_flow.outcome_for(_OK_ID, ok_id=_OK_ID, finish_first_id=finish_first_id)

    assert outcome is quit_flow.QuitOutcome.QUIT


# --- running_exit_message: the exit_running_dlg copy (E5.2.3) -----


def test_running_exit_message_interpolates_the_ride_name_and_wall_clock() -> None:
    """The message names the ride and keeps the wall-clock copy."""
    message = quit_flow.running_exit_message("GORBA EPIC 2026")

    assert "GORBA EPIC 2026" in message
    assert "wall clock" in message
    assert len(message) > 40


def test_running_exit_message_given_different_rides_differs_per_ride() -> None:
    """Each ride gets its own message naming that ride."""
    first = quit_flow.running_exit_message("GORBA EPIC 2026")
    second = quit_flow.running_exit_message("Club poker night")

    assert first != second
    assert "Club poker night" in second
    assert "GORBA EPIC 2026" not in second


@given(st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=30))
def test_running_exit_message_given_any_ride_name_contains_it(ride_name: str) -> None:
    """Property: any non-empty ride name lands verbatim in the copy."""
    message = quit_flow.running_exit_message(ride_name)

    assert message.startswith(ride_name)
    assert "wall clock" in message
