# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for E8.1.1 settings persistence (VM-only, Phase 10).

Every case runs in a fresh, spawned interpreter via
``console_subprocess_scenarios.py`` (``scenario_runner.run_scenario``),
following that module's own isolation rationale exactly: the scenario
constructs the app (``rivercrossing.ui.app.build_main_window``), which
needs a live desktop session -- so this file runs only in the Tart VM,
never on this host (binding rule 1: no desktop-interacting test is run
here, only written).

The scenario writes its settings to a temp dir, never the real user
config dir (E8.1.1's own rule), and reports raw facts for this module
to assert -- a wrong measured value surfaces as a normal pytest
assertion diff, not a bare non-zero exit code.
"""

import pytest
import scenario_runner

pytestmark = pytest.mark.functional

# R-37 with hide-times ON: the Lap time/Total columns vanish, leaving
# these five. Mirrors test_console_demo.py's own pinned spelling.
_HIDDEN_TIMES_COLUMNS = ["Time", "Plate", "Entry", "Lap", "Card"]


def test_settings_persistence_applies_and_round_trips_every_control_through_a_relaunch() -> None:
    """Set -> relaunch -> read: every setting survives (E8.1.1).

    Includes the splitter sash and the frame geometry -- the two
    layout settings that have no dialog control. The scenario reports
    the values applied from a pre-saved file in run one, the values
    its own mutations saved, and the values a fresh build restores
    after that run closed.
    """
    result = scenario_runner.run_scenario("settings_persistence_round_trip")

    assert result["ok"], result["context"]
    data = result["data"]
    assert data["applied_dark_radio"] is True, result["context"]
    assert data["applied_sound_muted"] is True, result["context"]
    assert data["applied_hide_times_columns"] == _HIDDEN_TIMES_COLUMNS, result["context"]
    assert data["applied_sash"] == 320, result["context"]
    assert data["applied_geometry"] == [40, 60, 1200, 800], result["context"]
    assert data["saved_sash_after_run1"] == 420, result["context"]
    assert data["saved_geometry_after_run1"] == [90, 110, 1250, 860], result["context"]
    assert data["relaunch_sash"] == 420, result["context"]
    assert data["relaunch_geometry"] == [90, 110, 1250, 860], result["context"]
