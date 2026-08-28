# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for the resume dialog + reopened banner (E5.2.2, R-52).

R-52: on launch with a running ride, ``resume_dlg`` always appears;
session bookkeeping distinguishes a clean quit from a crash and words
the dialog accordingly (spec §3). Continue resumes the store-loaded
ride with correct elapsed (the engine's replayed ``actual_start`` and
the wall clock, R-30); Open library opens ``ride_library_dlg``
instead; and resuming a REOPENED ride shows the code-constructed,
``SetName()``-named ``reopened_infobar`` (the corrections banner, §13).

The wording's headline assertions mirror task-brief E5.2.2: the
``message_lbl`` is non-empty and contains the ride name -- a blank
label is a failed assertion, never a cosmetic one.

Like ``test_exit_running_dlg.py``, every scenario mutates process-
global state (the resume dialog is a bootstrap modal, and Continue
rebuilds the console around the store engine), so each runs in a
fresh, spawned interpreter via ``console_subprocess_scenarios.py``
(its own module docstring records the address-reuse and modal-hang
reasons that force the subprocess pattern).
"""

import pytest
import scenario_runner

pytestmark = pytest.mark.functional


def test_resume_dlg_given_running_at_exit_shows_quit_wording_naming_the_ride() -> None:
    """RUNNING_AT_EXIT launch: resume_dlg appears with the quit copy."""
    result = scenario_runner.run_scenario("resume_dlg_quit_wording_shows")

    data = result["data"]
    assert data["resume_dlg_shown"] is True, result["context"]
    # The copy is never blank and names the ride (task-brief E5.2.2).
    assert data["message_lbl"] != "", result["context"]
    assert "GORBA EPIC 2026" in data["message_lbl"], result["context"]
    # spec §3's quit wording, with the pinned local 24-hour time.
    assert "You quit at 12:41" in data["message_lbl"], result["context"]
    # continue_btn is the marked default so Enter resumes (R-76).
    assert data["continue_is_default"] == "continue_btn", result["context"]


def test_resume_dlg_given_crashed_with_ride_shows_crash_wording_naming_the_ride() -> None:
    """CRASHED-with-ride launch: resume_dlg shows the crash copy."""
    result = scenario_runner.run_scenario("resume_dlg_crash_wording_shows")

    data = result["data"]
    assert data["resume_dlg_shown"] is True, result["context"]
    assert data["message_lbl"] != "", result["context"]
    assert "GORBA EPIC 2026" in data["message_lbl"], result["context"]
    # spec §3's crash wording, from the pinned last heartbeat.
    assert "closed unexpectedly at 12:37" in data["message_lbl"], result["context"]


def test_resume_continue_resumes_the_ride_with_correct_elapsed() -> None:
    """Continue loads the store ride; the clock shows true elapsed."""
    result = scenario_runner.run_scenario("resume_continue_loads_ride_with_elapsed")

    data = result["data"]
    # Started at 10:00, fixed launch clock at 11:00 -> 1:00:00 elapsed
    # (R-30: the wall clock kept counting while the app was closed).
    assert data["clock_elapsed"] == data["expected_elapsed"] == "1:00:00", result["context"]
    assert data["status_label"] == "RUNNING", result["context"]


def test_resume_open_library_opens_ride_library_dlg() -> None:
    """library_btn on resume_dlg opens ride_library_dlg instead."""
    result = scenario_runner.run_scenario("resume_library_opens_ride_library")

    data = result["data"]
    assert data["resume_dlg_shown"] is True, result["context"]
    assert data["library_shown"] is True, result["context"]


def test_resume_reopened_ride_shows_the_reopened_infobar_by_name() -> None:
    """Continue a REOPENED ride: the corrections banner shows."""
    result = scenario_runner.run_scenario("resume_reopened_ride_shows_reopened_infobar")

    data = result["data"]
    assert data["infobar_resolves"] is True, result["context"]
    assert data["infobar_shown"] is True, result["context"]
    assert data["status_label"] == "REOPENED", result["context"]
