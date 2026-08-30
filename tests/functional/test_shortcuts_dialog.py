# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for E8.2.1's shortcuts dialog (VM-only, Phase 10).

Every case runs in a fresh, spawned interpreter via
``console_subprocess_scenarios.py`` (``scenario_runner.run_scenario``),
following that module's own isolation rationale exactly: the dialog is
driven through the real app bootstrap, which needs a live desktop
session -- so this file runs only in the Tart VM, never on this host.

The scenarios build the app hermetically (the per-scenario tmp
settings path added for the E8.1 VM-regression fixes), so they never
read or write the real user config dir. They report raw facts for
this module to assert -- a wrong measured value surfaces as a normal
pytest assertion diff, not a bare non-zero exit code.
"""

import pytest
import scenario_runner

from rivercrossing.ui.accelerators import ACCELERATOR_TABLE

pytestmark = pytest.mark.functional


def _table_rows() -> list[list[str]]:
    """Return ``ACCELERATOR_TABLE`` as ``[key, action]`` row pairs.

    The expected dialog content, derived from the single source of
    truth rather than hand-copied (the dialog's whole point).
    """
    return [[accel.key, accel.action] for accel in ACCELERATOR_TABLE]


def test_shortcuts_dialog_shows_the_four_accelerator_rows_via_the_menu_route() -> None:
    """Help ▸ Keyboard Shortcuts lists the accelerator table in order.

    E8.2.1's route half (a + c): firing ``mi_shortcuts`` opens
    ``shortcuts_dlg`` whose ``shortcuts_list`` renders one Key | Action
    row per :class:`Accelerator` in table order, and Escape closes it
    through ``wire_close_button``'s ``wxID_CLOSE`` binding (the dialog
    is destroyed by ``_open_target``).
    """
    result = scenario_runner.run_scenario("shortcuts_dialog_route_shows_the_accelerator_table")

    assert result["ok"], result["context"]
    data = result["data"]
    assert data["dlg_shown"] is True, result["context"]
    assert data["column_titles"] == ["Key", "Action"], result["context"]
    assert data["rows"] == _table_rows(), result["context"]
    assert data["dialog_destroyed"] is True, result["context"]


def test_shortcuts_dialog_renders_an_injected_fake_row() -> None:
    """A constructed dialog renders its rows input, not hard-coded four.

    E8.2.1's generativity half (b): ``rows`` is a constructor seam, and
    an injected fake accelerator must appear alongside a real table
    row -- proving the dialog renders its input.
    """
    result = scenario_runner.run_scenario("shortcuts_dialog_renders_injected_rows")

    assert result["ok"], result["context"]
    data = result["data"]
    assert data["row_count"] == 2, result["context"]
    assert data["rows"][0] == ["Enter", "Record crossing for typed plate"], result["context"]
    assert ["Ctrl+Alt+K", "Fake action"] in data["rows"], result["context"]
