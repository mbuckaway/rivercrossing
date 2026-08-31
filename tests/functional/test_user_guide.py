# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for E8.2.2's Help ▸ User Guide (VM-only, Phase 10).

Every case runs in a fresh, spawned interpreter via
``console_subprocess_scenarios.py`` (``scenario_runner.run_scenario``),
following that module's own isolation rationale exactly: the route is
driven through the real app bootstrap, which needs a live desktop
session -- so this file runs only in the Tart VM, never on this host.

The scenarios monkeypatch ``rivercrossing.ui.help.webbrowser.open``
-- an I/O boundary (T-10) -- and report the captured URLs as raw
facts for this module to assert; a wrong measured anchor surfaces as
a normal pytest assertion diff, not a bare non-zero exit code. The
expected URL is derived from the single source of truth
(:func:`rivercrossing.ui.help.guide_path` plus the anchor map), never
hand-copied.
"""

import pytest
import scenario_runner

from rivercrossing.ui import help as help_module
from rivercrossing.ui import ids

pytestmark = pytest.mark.functional


def _expected_url(anchor: str) -> str:
    """Return the guide's ``file://`` URL at *anchor*."""
    return f"{help_module.guide_path().as_uri()}#{anchor}"


def test_user_guide_from_settings_dialog_opens_the_settings_anchor() -> None:
    """F1 with settings_dlg open deep-links to the Settings chapter."""
    result = scenario_runner.run_scenario("user_guide_from_settings_dialog_opens_anchor")

    assert result["ok"], result["context"]
    data = result["data"]
    expected = _expected_url(help_module.ANCHOR_BY_WINDOW[ids.SETTINGS_DLG])
    assert data["dlg_shown"] is True, result["context"]
    assert data["url"] == expected, result["context"]
    assert data["status_text"] == f"Opened user guide: {expected}", result["context"]


def test_user_guide_from_about_dialog_opens_the_about_anchor() -> None:
    """F1 with about_dlg open deep-links to the About chapter."""
    result = scenario_runner.run_scenario("user_guide_from_about_dialog_opens_anchor")

    assert result["ok"], result["context"]
    data = result["data"]
    expected = _expected_url(help_module.ANCHOR_BY_WINDOW[ids.ABOUT_DLG])
    assert data["dlg_shown"] is True, result["context"]
    assert data["url"] == expected, result["context"]


def test_user_guide_from_shortcuts_dialog_opens_the_shortcuts_anchor() -> None:
    """F1 with shortcuts_dlg open deep-links to Appendix A."""
    result = scenario_runner.run_scenario("user_guide_from_shortcuts_dialog_opens_anchor")

    assert result["ok"], result["context"]
    data = result["data"]
    expected = _expected_url(help_module.ANCHOR_BY_WINDOW[ids.SHORTCUTS_DLG])
    assert data["dlg_shown"] is True, result["context"]
    assert data["url"] == expected, result["context"]


def test_user_guide_with_no_dialog_open_uses_the_default_anchor() -> None:
    """F1 from the main frame alone uses the default anchor."""
    result = scenario_runner.run_scenario("user_guide_with_no_dialog_opens_default_anchor")

    assert result["ok"], result["context"]
    data = result["data"]
    assert data["url"] == _expected_url(help_module.DEFAULT_ANCHOR), result["context"]
