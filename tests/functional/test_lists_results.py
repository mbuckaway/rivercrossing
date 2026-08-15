# SPDX-License-Identifier: GPL-3.0-only
"""Real-toolkit tests for ``results_frame``'s demo display (E1.5.2).

Split out of ``test_lists_demo.py`` -- alongside
``test_lists_entry_detail.py`` -- so the two heaviest functional
files spread their per-worker window churn across ``--dist loadfile``
workers (the wrapper-cache corruption remedy). ``ResultsWindow``
decorates an already-XRC-loaded window (``harness.load_window``, the
same pattern ``test_console_demo.py`` uses for ``MainFrame``) with
the code-side bindings xrc-windows.md's per-window footnotes assign
to it: the standings DataView's columns and rows and the five
publish-checkbox defaults authored in results.xrc.

The window carries no splitter, so the rebuild-and-compare hazard
this suite's harness warns about does not apply; it is built exactly
once per module and never torn down and rebuilt mid-module. The
constants and helpers shared with the other two list-window files
live in ``_lists_common``.
"""

import re

import harness
import pytest
from _lists_common import (
    CANVAS_PUBLISH_DEFAULTS,
    CANVAS_STANDINGS,
    MAX_SCREEN_HEIGHT,
    MAX_SCREEN_WIDTH,
    _model_row,
    _spy_repaint,
)

from rivercrossing.demo import DemoDataSource
from rivercrossing.ui import ids
from rivercrossing.ui.presenters.data_source import StandingsRow
from rivercrossing.ui.views import results_win
from rivercrossing.ui.views.results_win import ResultsWindow

pytestmark = pytest.mark.functional


# ----------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def shared_results(xrc_resource: object) -> ResultsWindow:
    """One ``ResultsWindow``, reused by every read-only assertion."""
    window = harness.load_window_verified(xrc_resource, ids.RESULTS_FRAME, frame=True)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        view = ResultsWindow(window, data_source=DemoDataSource())
        yield view
    finally:
        harness.close_window(window)


# ------------------------------------------------------ results_frame


def test_results_window_shows_three_standings_matching_the_canvas_exactly(
    shared_results: ResultsWindow,
) -> None:
    """xrc-windows.md D: 77/Trail Blazers, 123/Ellis, 8/Dubois."""
    model = shared_results.standings_list.GetModel()

    rows = tuple(_model_row(model, row, range(7)) for row in range(model.GetCount()))

    assert rows == CANVAS_STANDINGS


def test_results_window_given_a_different_source_shows_its_rows_not_the_demo(
    xrc_resource: object,
) -> None:
    """Req 6: a no-op binding would keep showing demo rows, not this."""

    class _StubSource:
        def standings(self) -> list[StandingsRow]:
            return [
                StandingsRow(
                    place=1,
                    plate="999",
                    entry="Stub Entry",
                    laps=1,
                    total="0:00:01",
                    best5=("2C", "2D", "2H", "2S", "3C"),
                    hand="Stub hand",
                )
            ]

    window = harness.load_window_verified(xrc_resource, ids.RESULTS_FRAME, frame=True)
    try:
        window.Show()
        harness.pump()
        view = ResultsWindow(window, data_source=_StubSource())
        model = view.standings_list.GetModel()
        row = _model_row(model, 0, range(7))
    finally:
        harness.close_window(window)

    assert row == ("1", "999", "Stub Entry", "1", "0:00:01", "2♣ 2♦ 2♥ 2♠ 3♣", "Stub hand")


@pytest.mark.parametrize(("checkbox_name", "expected"), CANVAS_PUBLISH_DEFAULTS)
def test_results_window_publish_checkbox_default_matches_the_authored_xrc(
    shared_results: ResultsWindow,
    checkbox_name: str,
    expected: bool,  # noqa: FBT001
) -> None:
    """Req 2: times off, laps board on, time board off, full/cards on.

    Asserted, never set here (results.xrc's own header comment
    records these five as already-authored canvas defaults) --
    proves the already-authored XRC, not any code this task wrote.
    """
    checkbox = harness.find_control(shared_results.frame, checkbox_name)

    assert checkbox.GetValue() is expected


def test_results_window_applies_the_canvas_minimum_width(shared_results: ResultsWindow) -> None:
    """D16: the canvas draws this window at 720px."""
    size = shared_results.frame.GetSize()

    assert size.width == results_win.MIN_SIZE[0]


def test_results_window_fits_within_1366x768(shared_results: ResultsWindow) -> None:
    """UX-DESKTOP §6: every window must fit the field-laptop floor."""
    size = shared_results.frame.GetSize()

    assert size.width <= MAX_SCREEN_WIDTH
    assert size.height <= MAX_SCREEN_HEIGHT


# ------------------------------------------------- negative path: _find


def test_results_window_find_given_an_unknown_control_name_raises_naming_it(
    shared_results: ResultsWindow,
) -> None:
    """T-5: the one ``raise`` in ``views/results_win.py``."""
    with pytest.raises(LookupError, match=re.escape("no control named 'no_such_control'")):
        shared_results._find("no_such_control")


# ------------------------------ repaint after model (unverified remedy)


def test_results_window_show_standings_repaints_the_list_after_associating_its_model(
    xrc_resource: object,
) -> None:
    """Unverified remedy; see ``associate_model``'s docstring."""
    window = harness.load_window_verified(xrc_resource, ids.RESULTS_FRAME, frame=True)
    try:
        window.Show()
        harness.pump()
        # control kept alive: _spy_repaint's docstring.
        control = harness.find_control(window, ids.STANDINGS_LIST)
        refresh, update = _spy_repaint(control)
        view = ResultsWindow(window, data_source=DemoDataSource())
        row_count = view.standings_list.GetModel().GetCount()
    finally:
        harness.close_window(window)

    assert row_count == len(CANVAS_STANDINGS)
    refresh.assert_called_once_with()
    update.assert_called_once_with()
