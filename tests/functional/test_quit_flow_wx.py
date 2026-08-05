# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for the quit / close / Dock-reopen flow (Phase 8, 8.5).

R-51/P8-D1: the app never exits without confirmation. Every scenario
here mutates process-global state -- ``wx.App.really_quitting``, the
red X's Veto()+Hide() and the ``MacReopenApp`` pair that only makes
sense against one live ``main_frame``, or ``wxEVT_QUERY_END_SESSION``
posted straight at the App -- so each runs its own scenario in a
fresh, spawned interpreter via ``console_subprocess_scenarios.py``,
through the shared :func:`scenario_runner.run_scenario` (Phase 10:
the spawn/decode/retry trio this module, ``test_theme.py`` and
``test_console_demo.py`` once each reproduced verbatim is now
extracted there, CODINGSTANDARDS-SIMPLECODE.md's rule of three).

Phase 10 also splits the red-X/Dock-reopen behaviour tests by
platform and adds their Windows counterpart: macOS never quits on the
close box (P8-D2, hides ``main_frame`` instead), while Windows' close
box runs the very same ``_confirm_quit`` flow File ▸ Exit does
(``app.py``'s ``_on_main_frame_close``, non-mac branch) -- the two
``windows_close_*`` scenarios below pin that documented contract.
They are red-first by design: they skip on this Mac and only run on
windows-latest CI.
"""

import sys

import pytest
import scenario_runner

pytestmark = pytest.mark.functional


def test_quit_menu_confirmed_destroys_the_frame() -> None:
    """wxID_EXIT + Quit on exit_running_dlg: the frame is destroyed."""
    result = scenario_runner.run_scenario("quit_menu_confirmed_destroys")

    assert result["data"] == {"frame_being_deleted": True}, result["context"]


def test_quit_menu_cancelled_leaves_the_frame_alive_and_shown() -> None:
    """wxID_EXIT + Cancel on exit_running_dlg: nothing closes."""
    result = scenario_runner.run_scenario("quit_menu_cancelled_stays")

    assert result["data"] == {"frame_being_deleted": False, "frame_shown": True}, result["context"]


def test_running_ride_shows_exit_running_dlg_on_exit() -> None:
    """The demo ride is RUNNING, so wxID_EXIT shows exit_running_dlg."""
    result = scenario_runner.run_scenario("running_ride_shows_exit_running_dlg")

    assert result["data"] == {"exit_running_dlg_shown": True}, result["context"]


def test_non_running_status_shows_exit_confirm_dlg_on_exit() -> None:
    """A DRAFT ride shows exit_confirm_dlg, not the running one."""
    result = scenario_runner.run_scenario("exit_confirm_dlg_shown_when_not_running")

    assert result["data"] == {"exit_confirm_dlg_shown": True}, result["context"]


def test_finish_first_btn_ends_dialog_stays_running_and_posts_notice() -> None:
    """A1: Finish ride first… ends the modal, ride stays running."""
    result = scenario_runner.run_scenario("finish_first_ends_dialog_stays_running_posts_notice")

    assert result["data"] == {
        "frame_being_deleted": False,
        "status_text": "Finish Ride… — not yet implemented",
    }, result["context"]


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason=(
        "P8-D2: hide-on-close is the macOS contract only -- Windows "
        "closes through the same quit confirmation File > Exit uses "
        "(pinned by this module's windows_close_* tests below)."
    ),
)
def test_red_x_close_vetoes_and_hides_the_frame_on_mac() -> None:
    """P8-D2: the red X never quits on macOS -- it hides main_frame."""
    result = scenario_runner.run_scenario("red_x_close_vetoes_and_hides_on_mac")

    assert result["data"] == {"being_deleted": False, "shown": False}, result["context"]


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason=(
        "P8-D2: hide-on-close, and so Dock-reopen, is the macOS "
        "contract only -- Windows closes through the same quit "
        "confirmation File > Exit uses and carries no Dock icon."
    ),
)
def test_mac_reopen_app_shows_and_raises_the_hidden_frame() -> None:
    """Dock-click reopen restores a hidden, non-iconized window."""
    result = scenario_runner.run_scenario("mac_reopen_shows_and_raises")

    assert result["data"] == {"shown_after_reopen": True}, result["context"]


def test_query_end_session_cancelled_vetoes_the_event() -> None:
    """Dock ▸ Quit, Cancel chosen: the session-end event is vetoed."""
    result = scenario_runner.run_scenario("query_end_session_cancelled_vetoes")

    assert result["data"] == {"vetoed": True}, result["context"]


def test_query_end_session_confirmed_does_not_veto_and_sets_the_flag() -> None:
    """Dock ▸ Quit, Quit: no veto -- keeps it quittable."""
    result = scenario_runner.run_scenario("query_end_session_confirmed_does_not_veto")

    assert result["data"] == {"vetoed": False, "really_quitting": True}, result["context"]


def test_forced_close_destroys_the_frame_without_opening_a_dialog() -> None:
    """Close(force=True) never runs the confirm flow at all."""
    result = scenario_runner.run_scenario("forced_close_destroys_without_dialog")

    assert result["data"] == {"being_deleted": True, "run_dialog_calls": 0}, result["context"]


def test_session_end_confirmed_then_close_destroys_with_no_second_dialog() -> None:
    """The really_quitting flag makes a follow-on plain Close() skip it.

    QUERY_END_SESSION's own default handler calls a plain (not
    forced) ``TopWindow->Close()`` next -- this is what keeps that
    second call from re-opening exit_running_dlg/exit_confirm_dlg.
    """
    result = scenario_runner.run_scenario("session_end_confirmed_then_close_destroys_once")

    assert result["data"] == {"being_deleted": True, "run_dialog_calls": 1}, result["context"]


@pytest.mark.skipif(
    sys.platform != "win32",
    reason=(
        "Documented Windows contract: the close box runs the same "
        "_confirm_quit flow as File > Exit (app.py's "
        "_on_main_frame_close, non-mac branch), unlike macOS's "
        "hide-on-close (P8-D2). RED-first: exercised on "
        "windows-latest CI, not this Mac."
    ),
)
def test_windows_close_cancelled_leaves_the_frame_alive_and_shown() -> None:
    """Windows ✕ + Cancel on exit_running_dlg: nothing closes."""
    result = scenario_runner.run_scenario("windows_close_cancelled_stays")

    assert result["data"] == {"frame_being_deleted": False, "frame_shown": True}, result["context"]


@pytest.mark.skipif(
    sys.platform != "win32",
    reason=(
        "Documented Windows contract: the close box runs the same "
        "_confirm_quit flow as File > Exit (app.py's "
        "_on_main_frame_close, non-mac branch), unlike macOS's "
        "hide-on-close (P8-D2). RED-first: exercised on "
        "windows-latest CI, not this Mac."
    ),
)
def test_windows_close_confirmed_destroys_the_frame() -> None:
    """Windows ✕ + Quit on exit_running_dlg: the frame is destroyed."""
    result = scenario_runner.run_scenario("windows_close_confirmed_destroys")

    assert result["data"] == {"frame_being_deleted": True}, result["context"]
