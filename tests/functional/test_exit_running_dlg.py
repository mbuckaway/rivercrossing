# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for the exit-with-running-ride dialog (E5.2.3, R-51).

R-51: quitting with a ride RUNNING opens ``exit_running_dlg`` with
exactly three buttons -- Cancel (the default, so a reflex Enter is
safe, §13/R-76), ``finish_first_btn`` and Quit-keep-running -- and its
``message_lbl`` interpolates the running ride's name (a blank label is
a failed assertion, per task-brief E5.2.1+E5.2.3). The three button
outcomes are: Cancel -> the app stays; ``finish_first_btn`` -> the
exit dialog ends and the E4.4.4 finish flow takes over (finish confirm
then ``presenter.on_finish``); Quit-keep-running -> ``close_session``
stamps ``closed_at``, the ride row is left untouched, and the app
quits -- the bookkeeping the E5.2.2 resume dialog will read on the
next launch.

Like ``test_quit_flow_wx.py``, every scenario here mutates
process-global state (a confirmed quit destroys ``main_frame`` and
the quit flag is set), so each runs in a fresh, spawned interpreter
via ``console_subprocess_scenarios.py`` (its own module docstring
records the address-reuse and modal-hang reasons that force the
subprocess pattern).
"""

import pytest
import scenario_runner

pytestmark = pytest.mark.functional


def test_exit_running_dlg_shows_three_buttons_named_message_and_cancel_default() -> None:
    """RUNNING quit shows the running variant: 3 buttons + ride copy."""
    result = scenario_runner.run_scenario("exit_running_dlg_probe_and_cancel")

    data = result["data"]
    assert data["exit_running_dlg_shown"] is True, result["context"]
    # message_lbl carries the running ride's name -- never blank.
    assert data["message_lbl"] != "", result["context"]
    assert "GORBA EPIC 2026" in data["message_lbl"], result["context"]
    # Exactly the three frozen buttons (spec §3/§15b, R-51).
    assert data["has_cancel"] is True, result["context"]
    assert data["has_finish_first"] is True, result["context"]
    assert data["has_quit"] is True, result["context"]
    # Cancel is the marked default so a reflex Enter is safe (R-76).
    assert data["default_name"] == "wxID_CANCEL", result["context"]


def test_exit_running_dlg_cancel_leaves_the_app_staying() -> None:
    """Cancel on exit_running_dlg: no quit, frame alive and shown."""
    result = scenario_runner.run_scenario("exit_running_dlg_probe_and_cancel")

    assert result["data"]["frame_being_deleted"] is False, result["context"]
    assert result["data"]["frame_shown"] is True, result["context"]


def test_exit_running_dlg_finish_first_routes_to_the_finish_flow() -> None:
    """finish_first_btn ends the exit dialog, runs the finish flow."""
    result = scenario_runner.run_scenario("finish_first_routes_to_the_finish_flow")

    assert result["data"] == {
        "finish_confirm_shown": True,
        "status_text": "Ride finished",
        "frame_being_deleted": False,
    }, result["context"]


def test_exit_running_dlg_quit_keep_running_stamps_closed_at_and_quits() -> None:
    """Quit-keep-running: session closed cleanly, ride untouched, quit.

    The store-backed app records the running ride on open (E5.2.1);
    the quit flow must stamp that session's ``closed_at``, never touch
    the ride row, and destroy the frame -- so the next launch reads
    RUNNING_AT_EXIT, not CRASHED.
    """
    result = scenario_runner.run_scenario("quit_keep_running_writes_closed_at_and_stays_running")

    assert result["data"] == {
        "frame_being_deleted": True,
        "session_state": "running_at_exit",
        "ride_status": "draft",  # untouched by the quit path
    }, result["context"]
