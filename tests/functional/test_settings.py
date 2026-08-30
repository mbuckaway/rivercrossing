# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for E8.1.1/E8.1.2 settings (VM-only, Phase 10).

Every case runs in a fresh, spawned interpreter via
``console_subprocess_scenarios.py`` (``scenario_runner.run_scenario``),
following that module's own isolation rationale exactly: the scenario
constructs the app (``rivercrossing.ui.app.build_main_window``), which
needs a live desktop session -- so this file runs only in the Tart VM,
never on this host (binding rule 1: no desktop-interacting test is run
here, only written).

The scenarios write their settings to temp dirs, never the real user
config dir (E8.1.1's own rule), and report raw facts for this module
to assert -- a wrong measured value surfaces as a normal pytest
assertion diff, not a bare non-zero exit code.

The two appearance-asserting cases split by platform exactly like
``test_theme.py``: macOS applies ``SetAppearance`` live, MSW returns
``CannotChange`` once ``main_frame`` exists, so only the platform-
independent facts (the menu radio, the saved file, the relaunch
render) are asserted on Windows.
"""

import sys

import pytest
import scenario_runner

from rivercrossing.ui.presenters.settings import ZOOM_LADDER

pytestmark = pytest.mark.functional

# R-37 with hide-times ON: the Lap time/Total columns vanish, leaving
# these five. Mirrors test_console_demo.py's own pinned spelling.
_HIDDEN_TIMES_COLUMNS = ["Time", "Plate", "Entry", "Lap", "Card"]

_DARWIN_ONLY = pytest.mark.skipif(
    sys.platform != "darwin",
    reason=(
        "macOS applies SetAppearance live at runtime (theme.py's own "
        "module docstring, P8-D4); MSW's CannotChange contract never "
        "lets an absolute dark/light appearance assertion hold."
    ),
)
_WIN32_ONLY = pytest.mark.skipif(
    sys.platform != "win32",
    reason=(
        "Documented Windows contract: MSW returns AppearanceResult."
        "CannotChange once main_frame already exists, so the theme "
        "never actually changes at runtime (theme.py's own module "
        "docstring). RED-first: exercised on windows-latest CI, not "
        "this Mac."
    ),
)


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


# --- E8.1.2: the settings dialog (appearance finish) -----------------


def test_settings_dialog_renders_the_persisted_values() -> None:
    """Open Settings: radios/checkboxes/choice reflect the saved file.

    The dialog must render what the app loaded -- light appearance,
    sound off, hide-times on, zoom 130 -- and rendering must itself
    change nothing (closed via Cancel).
    """
    result = scenario_runner.run_scenario("settings_dialog_renders_persisted_values")

    assert result["ok"], result["context"]
    data = result["data"]
    assert data["dlg_shown"] is True, result["context"]
    assert data["rendered_system"] is False, result["context"]
    assert data["rendered_light"] is True, result["context"]
    assert data["rendered_dark"] is False, result["context"]
    assert data["rendered_sound"] is False, result["context"]
    assert data["rendered_hide_times"] is True, result["context"]
    assert data["rendered_zoom_selection"] == ZOOM_LADDER.index(130), result["context"]


@_DARWIN_ONLY
def test_settings_dialog_ok_applies_and_persists_dark_on_mac() -> None:
    """Toggle Dark + OK: live apply, menu radio, file, relaunch render.

    The appearance mirror both ways: the dialog's OK applies Dark live
    (SystemAppearance flips), re-checks the View-menu radio, writes the
    file, and a relaunch still renders Dark in a fresh dialog. Sound
    off and hide-times on ride the same OK.
    """
    result = scenario_runner.run_scenario("settings_dialog_ok_applies_and_persists_dark")

    assert result["ok"], result["context"]
    data = result["data"]
    assert data["dlg_shown"] is True, result["context"]
    assert data["is_dark_after"] is True, result["context"]
    assert data["menu_dark_checked"] is True, result["context"]
    assert data["sound_muted_after"] is True, result["context"]
    assert data["hide_times_columns"] == _HIDDEN_TIMES_COLUMNS, result["context"]
    assert data["saved_appearance"] == "dark", result["context"]
    assert data["saved_sound_on"] is False, result["context"]
    assert data["saved_hide_times"] is True, result["context"]
    assert data["relaunch_dlg_shown"] is True, result["context"]
    assert data["relaunch_dark"] is True, result["context"]


@_WIN32_ONLY
def test_settings_dialog_ok_persists_dark_where_runtime_cannot_change_on_windows() -> None:
    """The dialog's OK persists even where SetAppearance cannot change.

    Never asserts ``is_dark_after``'s absolute value -- the CannotChange
    contract (theme.py's own module docstring) only documents that the
    call has no runtime effect, not the pre-existing OS appearance. The
    mirror facts (menu radio, saved file, relaunch render) hold on
    every platform.
    """
    result = scenario_runner.run_scenario("settings_dialog_ok_applies_and_persists_dark")

    assert result["ok"], result["context"]
    data = result["data"]
    assert data["dlg_shown"] is True, result["context"]
    assert data["menu_dark_checked"] is True, result["context"]
    assert data["sound_muted_after"] is True, result["context"]
    assert data["hide_times_columns"] == _HIDDEN_TIMES_COLUMNS, result["context"]
    assert data["saved_appearance"] == "dark", result["context"]
    assert data["saved_sound_on"] is False, result["context"]
    assert data["saved_hide_times"] is True, result["context"]
    assert data["relaunch_dlg_shown"] is True, result["context"]
    assert data["relaunch_dark"] is True, result["context"]


def test_settings_dialog_cancel_applies_and_persists_nothing() -> None:
    """Toggle then Cancel: no apply, no menu change, file untouched.

    Platform-independent: "no change" is the documented contract on
    both macOS (the toggle never reached OK) and MSW (where live change
    is impossible anyway) -- the pre-existing LIGHT appearance, light
    menu radio, sound on and the untouched file all read as they did
    before the dialog opened.
    """
    result = scenario_runner.run_scenario("settings_dialog_cancel_applies_nothing")

    assert result["ok"], result["context"]
    data = result["data"]
    assert data["dlg_shown"] is True, result["context"]
    assert data["appearance_unchanged"] is True, result["context"]
    assert data["menu_dark_checked"] is False, result["context"]
    assert data["sound_muted_after"] is False, result["context"]
    assert data["saved_appearance"] == "light", result["context"]
    assert data["saved_sound_on"] is True, result["context"]
