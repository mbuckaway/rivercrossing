# SPDX-License-Identifier: GPL-3.0-only
"""Functional tests for selftest_dlg (E2.4.1): the real evaluator suite.

Drives the actual ``selftest_dlg`` XRC dialog wired to
``rivercrossing.ui.views.selftest.SelfTestDialog``, which runs the
real ``rivercrossing.hands.self_test`` -- never a stub -- so a green
run here is the task brief's own named harness test: "dialog runs
real suite, output lines end PASS, rerun works"; the negative
corrupts the loader seam :func:`self_test` itself reads through, so a
broken table shows a FAIL line and the hook reports red, exactly as
E2.4.1's unit suite (``tests/unit/test_hands.py``) already pins
headless -- this file is the same claim, through the real dialog.
"""

import re
from typing import Any

import harness
import pytest

from rivercrossing import hands
from rivercrossing.ui import ids
from rivercrossing.ui.views.selftest import SelfTestDialog

pytestmark = pytest.mark.functional

# The whole-field timing line's own detail is a freshly *measured*
# duration (hands.py's self_test()), which legitimately differs
# between two real runs -- normalized away before comparing two
# renders for equality (see test_selftest_dlg_rerun_btn_reruns_and_
# refreshes_the_output), never before comparing a single render.
_DURATION_TOKEN = re.compile(r"\d+\.\d+ s")


def _show(xrc_resource: Any) -> tuple[Any, SelfTestDialog]:  # noqa: ANN401 -- wx ships no stubs
    """Load selftest_dlg, wire it live, show it, and pump once."""
    dialog = harness.load_window(xrc_resource, ids.SELFTEST_DLG, frame=False)
    view = SelfTestDialog(dialog)
    dialog.Show()
    harness.pump()
    return dialog, view


def _output_lines(dialog: Any) -> list[str]:  # noqa: ANN401 -- wx ships no stubs
    """Split ``selftest_output``'s current text into its own lines."""
    return harness.find_control(dialog, ids.SELFTEST_OUTPUT).GetValue().splitlines()


def _normalize_duration(line: str) -> str:
    """Replace a rendered "N.NN s" duration with a stable token."""
    return _DURATION_TOKEN.sub("<duration>", line)


def test_selftest_dlg_runs_the_real_suite_and_every_line_ends_pass(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Opening selftest_dlg runs the real evaluator suite, all green."""
    dialog, view = _show(xrc_resource)

    try:
        lines = _output_lines(dialog)
    finally:
        harness.close_window(dialog)

    assert view.presenter.report.passed is True
    assert len(lines) == len(view.presenter.report.checks)
    assert all(line.endswith("PASS") for line in lines)


def test_selftest_dlg_rerun_btn_reruns_and_refreshes_the_output(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Clicking rerun_btn reruns the real suite, re-rendering it.

    Each run measures the whole-field timing check's own duration
    fresh, so the rendered detail legitimately differs run to run
    (measured: 0.03 s vs 0.11 s in the CI VM) -- lines are compared
    with that one token normalized away, pinning full re-render
    semantics (same four names, same shape, same PASS status)
    without depending on wall-clock equality between two real runs.
    """
    dialog, view = _show(xrc_resource)
    first_lines = _output_lines(dialog)
    first_report = view.presenter.report

    try:
        harness.click(dialog, ids.RERUN_BTN)
        second_lines = _output_lines(dialog)
    finally:
        harness.close_window(dialog)

    assert view.presenter.report.passed is True
    assert view.presenter.report is not first_report
    assert [_normalize_duration(line) for line in second_lines] == [
        _normalize_duration(line) for line in first_lines
    ]
    assert all(line.endswith("PASS") for line in second_lines)


def test_selftest_dlg_given_a_broken_rank_sweep_table_shows_a_fail_line(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A corrupted loader seam turns the sweep line FAIL, hook red.

    Monkeypatches the same module-level loader
    ``tests/unit/test_hands.py`` targets -- never a "test mode" flag
    on ``self_test`` itself -- so this proves the real dialog surfaces
    a genuine failure, not merely that it can render one.
    """
    monkeypatch.setattr(hands, "_load_rank_sweep_vectors", lambda: (("2C 3D 4H 5S 6C", 1),))
    dialog, view = _show(xrc_resource)

    try:
        lines = _output_lines(dialog)
    finally:
        harness.close_window(dialog)

    assert view.presenter.report.passed is False
    assert lines[0].endswith("FAIL")
