# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for E8.2.3's About box (VM-only, Phase 10).

Every case runs in a fresh, spawned interpreter via
``console_subprocess_scenarios.py`` (``scenario_runner.run_scenario``),
following that module's own isolation rationale exactly: the dialog is
driven through the real app bootstrap, which needs a live desktop
session -- so this file runs only in the Tart VM, never on this host.

The scenarios report raw facts (version text, gorba link URL, bitmap
IsOk/size, logo-match) for this module to assert; a wrong measured
value surfaces as a normal pytest assertion diff, not a bare non-zero
exit code.
"""

import pytest
import scenario_runner

from rivercrossing import __version__

pytestmark = pytest.mark.functional


def test_about_dialog_opens_with_the_package_version_and_gorba_link() -> None:
    """Help ▸ About renders version_lbl and gorba_link (E8.2.3 a).

    Also proves the fallback logo path (E8.2.3 b): the bootstrap
    threads no ride logo, so ``about_logo_bmp`` must be a non-null
    bitmap -- wx.NullBitmap is never acceptable (dialogs.xrc).
    """
    result = scenario_runner.run_scenario("about_dialog_route_renders_version_and_gorba_link")

    assert result["ok"], result["context"]
    data = result["data"]
    assert data["dlg_shown"] is True, result["context"]
    assert data["version_text"] == __version__, result["context"]
    assert data["gorba_is_hyperlink"] is True, result["context"]
    assert data["gorba_url"] == "https://gorba.ca", result["context"]
    assert data["logo_bitmap_ok"] is True, result["context"]


def test_about_dialog_uses_the_ride_logo_bitmap_when_one_is_present() -> None:
    """A real logo_path sets about_logo_bmp to that file's bitmap."""
    result = scenario_runner.run_scenario("about_dialog_uses_the_ride_logo_bitmap")

    assert result["ok"], result["context"]
    data = result["data"]
    assert data["logo_bitmap_ok"] is True, result["context"]
    assert data["logo_matches_file"] is True, result["context"]
